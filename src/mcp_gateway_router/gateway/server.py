"""The MCP server the client talks to.

``Gateway`` is protocol-independent and holds the logic; ``build_app`` binds it to the
MCP SDK. Keeping them apart is what lets the behaviour be tested without a transport.

**On the SDK binding.** ``mcp`` 2.x dropped the 1.x ``@server.list_tools()`` decorators.
``MCPServer`` is the high-level replacement, and its protocol handlers dispatch to
``self.list_tools()`` and ``self.call_tool()`` — so a subclass that overrides both is the
supported way to serve a tool list that changes at runtime.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..catalog import Tool
from ..selector import DecisionContext
from .config import GatewayConfig
from .environment import safe_capture_environment
from .log import EXPOSURE_LISTED, EXPOSURE_UNEXPOSED, ExposureLog
from .naming import advertised_name, parse_advertised
from .policy import Policy
from .selectors import build_selector
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
        # Take the id from the log so the filename and the records agree.
        self._session_id = log.session_id
        self._exposed: set[tuple[str, str]] = set()
        self._called: list[tuple[str, str]] = []
        # Captured once per session: it describes the session, not the request, and
        # the git lookups shouldn't run on every tools/list.
        self._environment = safe_capture_environment()

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
            environment=self._environment,
            # Injected by the Q1 harness. The MCP protocol supplies no prompt at
            # tools/list, so a task here means a benchmark deliberately handed us one —
            # which is exactly how an arm becomes decision point C instead of A.
            task=os.environ.get("MCP_GATEWAY_TASK") or None,
        )

    def _rewrite(self, tools: list[Tool]) -> list[Tool]:
        """Apply ``description_overrides`` to the advertised set.

        Applied *after* selection, never before: the override changes what the client's
        ranker reads, not what our own selector scored. Rewriting first would make the
        two arms differ in two places at once.

        Identity is untouched — ``server_id`` and ``name`` are what routing and the log
        key on, so a rewritten tool still calls through and still reconciles against the
        catalog. Only the text moves.
        """
        overrides = self._config.description_overrides
        if not overrides:
            return tools
        return [
            dataclasses.replace(t, description=overrides[f"{t.server_id}/{t.name}"])
            if f"{t.server_id}/{t.name}" in overrides
            else t
            for t in tools
        ]

    async def list_tools(self) -> list[Tool]:
        catalog = await self._pool.aggregate()
        exposed = self._policy.decide(catalog, self._pool.catalog_hash(), self._context())
        # Keys, not rewritten tools: `was_exposed` must survive a description change.
        self._exposed = {t.key for t in exposed}
        return self._rewrite(exposed)

    def _augment(self, result: Any, key: tuple[str, str]) -> Any:
        """Append a gateway suggestion to a tool result.

        This is the only lever that acts *after* a call, and therefore the only one
        with within-session state to act on. It is also the only one that can surface
        a tool the client's ranker would never return — it bypasses retrieval instead
        of trying to influence it, which the description probe showed is impossible.

        **Fails open, and silently.** A suggestion is an optimisation; a tool result is
        the answer the user is waiting for. If the result isn't the shape we expect —
        a different SDK version, an upstream returning something exotic — the original
        is returned untouched rather than risking the call over a hint.

        The text is prefixed so it is unambiguously ours. We are injecting content into
        our own client's context, which is legitimate for a trusted proxy and would not
        be if it were indistinguishable from what the upstream said.
        """
        suggestion = self._config.result_suggestions.get(f"{key[0]}/{key[1]}")
        if not suggestion:
            return result
        try:
            from mcp.types import TextContent

            content = getattr(result, "content", None)
            if isinstance(content, list):
                content.append(TextContent(type="text", text=suggestion))
        except Exception:
            logger.warning("could not attach suggestion to %s/%s result", *key)
        return result

    async def call_tool(self, advertised: str, arguments: dict) -> Any:
        server_id, tool_name = parse_advertised(advertised)
        key = (server_id, tool_name)
        started = time.monotonic()
        status = "ok"
        try:
            result = await self._pool.call(server_id, tool_name, arguments)
            return self._augment(result, key)
        except Exception:
            status = "error"
            raise
        finally:
            self._called.append(key)
            self._log.call(
                session_id=self._session_id,
                tool_uid=f"{server_id}/{tool_name}",
                exposure=EXPOSURE_LISTED if key in self._exposed else EXPOSURE_UNEXPOSED,
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
    config = GatewayConfig.from_file(config_path)
    pool = UpstreamPool(config.upstreams)
    await pool.start()

    log = ExposureLog(config.log_dir)
    counter = build_counter(config)

    selector = build_selector(config.selector, config.pinned)
    policy = Policy(config, selector, counter, log)
    gateway = Gateway(config, pool, policy, log)
    app = build_app(gateway)

    try:
        await app.run_stdio_async()
    finally:
        await pool.aclose()
        log.close()
