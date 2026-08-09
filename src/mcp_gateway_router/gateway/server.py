"""The MCP server the client talks to.

``Gateway`` is protocol-independent and holds the logic; ``build_app`` binds it to the
MCP SDK. Keeping them apart is what lets the behaviour be tested without a transport.

**On the SDK binding.** ``mcp`` 2.x dropped the 1.x ``@server.list_tools()`` decorators.
``MCPServer`` is the high-level replacement, and its protocol handlers dispatch to
``self.list_tools()`` and ``self.call_tool()`` — so a subclass that overrides both is the
supported way to serve a tool list that changes at runtime.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from ..catalog import Tool
from ..selector import DecisionContext
from .config import GatewayConfig
from .log import ExposureLog
from .naming import advertised_name, parse_advertised
from .policy import Policy
from .upstream import UpstreamPool

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(
        self,
        config: GatewayConfig,
        pool: UpstreamPool,
        policy: Policy,
        log: ExposureLog,
    ) -> None:
        self._config = config
        self._pool = pool
        self._policy = policy
        self._log = log
        self._session_id = f"s-{int(time.time())}"
        self._exposed: set[tuple[str, str]] = set()
        self._called: list[tuple[str, str]] = []

    def _context(self) -> DecisionContext:
        """What the gateway knows at ``tools/list``.

        There is no prompt here — the client has not sent one and the protocol offers
        no channel for it. ``task`` stays ``None``, which is what makes this decision
        point A.
        """
        return DecisionContext(
            session_id=self._session_id,
            client_name="claude-code",
            tools_called=tuple(self._called),
        )

    async def list_tools(self) -> list[Tool]:
        catalog = await self._pool.aggregate()
        exposed = self._policy.decide(catalog, self._pool.catalog_hash(), self._context())
        self._exposed = {t.key for t in exposed}
        return exposed

    async def call_tool(self, advertised: str, arguments: dict) -> Any:
        server_id, tool_name = parse_advertised(advertised)
        key = (server_id, tool_name)
        started = time.monotonic()
        status = "ok"
        try:
            return await self._pool.call(server_id, tool_name, arguments)
        except Exception:
            status = "error"
            raise
        finally:
            self._called.append(key)
            self._log.call(
                session_id=self._session_id,
                tool_uid=f"{server_id}/{tool_name}",
                was_exposed=key in self._exposed,
                status=status,
                latency_ms=int((time.monotonic() - started) * 1000),
            )


def build_app(gateway: Gateway):
    """Bind a ``Gateway`` to the MCP SDK's high-level server."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import Tool as MCPTool

    class _GatewayApp(MCPServer):
        async def list_tools(self) -> list[MCPTool]:
            return [
                MCPTool(
                    name=advertised_name(tool),
                    description=tool.description,
                    input_schema=tool.input_schema
                    or {"type": "object", "properties": {}},
                )
                for tool in await gateway.list_tools()
            ]

        async def call_tool(self, name: str, arguments: dict, context=None) -> Any:
            # The upstream ClientSession already returns a CallToolResult, which is
            # exactly what this handler is expected to produce. Pass it straight
            # through rather than unwrapping and rebuilding it.
            return await gateway.call_tool(name, arguments)

    return _GatewayApp("mcp-gateway")


DEFAULT_TOKEN_COST = 100
CHARS_PER_TOKEN = 3.4  # measured on the harvested catalog; dense JSON schemas


def build_counter(config: GatewayConfig):
    """Token costs for the data plane, read from the harvested catalog.

    **This never calls the Anthropic API.** Answering ``tools/list`` happens on every
    session open; measuring costs there would put a network round trip — and an
    account-balance dependency — in the critical path of the proxy. Measurement is a
    control-plane job: ``harvest.py`` runs ``count_tokens`` once and writes
    ``results/catalog.json``; the gateway reads it.

    Falls back to a character-length estimate for tools absent from the artifact, so
    a stale or missing catalog degrades cost accuracy rather than availability.
    """
    from ..tokens import StaticTokenCounter

    costs: dict[str, int] = {}
    if config.catalog_path is not None and config.catalog_path.exists():
        payload = json.loads(config.catalog_path.read_text())
        costs = {k: int(v) for k, v in payload.get("costs", {}).items()}
        logger.info("loaded %d measured token costs from %s", len(costs), config.catalog_path)
    else:
        logger.warning(
            "no harvested catalog at %s; token costs are estimated from schema length",
            config.catalog_path,
        )

    class _CatalogCounter(StaticTokenCounter):
        def cost(self, tool: Tool) -> int:
            measured = self._costs.get(tool.uid)
            if measured is not None:
                return measured
            body = tool.name + tool.description + json.dumps(
                tool.input_schema, separators=(",", ":")
            )
            return max(1, int(len(body) / CHARS_PER_TOKEN))

    return _CatalogCounter(costs, default=DEFAULT_TOKEN_COST)


async def serve(config_path: Path) -> None:
    from ..baselines import StaticSet

    config = GatewayConfig.from_file(config_path)
    pool = UpstreamPool(config.upstreams)
    await pool.start()

    log = ExposureLog(config.log_path)
    counter = build_counter(config)

    selector = StaticSet(config.pinned)
    policy = Policy(config, selector, counter, log)
    gateway = Gateway(config, pool, policy, log)
    app = build_app(gateway)

    try:
        await app.run_stdio_async()
    finally:
        await pool.aclose()
        log.close()
