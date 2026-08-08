"""The MCP server the client talks to.

``Gateway`` is protocol-independent and holds the logic; ``build_app`` binds it to the
MCP SDK. Keeping them apart is what lets the behaviour be tested without a transport.

**On the SDK binding.** ``mcp`` 2.x dropped the 1.x ``@server.list_tools()`` decorators.
``MCPServer`` is the high-level replacement, and its protocol handlers dispatch to
``self.list_tools()`` and ``self.call_tool()`` — so a subclass that overrides both is the
supported way to serve a tool list that changes at runtime.
"""

from __future__ import annotations

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


async def serve(config_path: Path) -> None:
    from ..baselines import StaticSet
    from ..tokens import AnthropicTokenCounter

    config = GatewayConfig.from_file(config_path)
    pool = UpstreamPool(config.upstreams)
    await pool.start()

    log = ExposureLog(config.log_path)
    counter = AnthropicTokenCounter(
        model=config.model,
        cache_path=config.log_path.parent / "token_costs.json",
    )

    selector = StaticSet(config.pinned)
    policy = Policy(config, selector, counter, log)
    gateway = Gateway(config, pool, policy, log)
    app = build_app(gateway)

    try:
        await app.run_stdio_async()
    finally:
        await pool.aclose()
        log.close()
