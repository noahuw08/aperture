"""Connections to the upstream MCP servers, and the aggregated catalog.

An upstream that fails to answer ``tools/list`` is skipped rather than fatal. The
gateway is in the critical path of every session: one broken server must degrade the
catalog, never take the client down.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Awaitable, Callable, Iterable, Protocol

from ..catalog import Catalog, Tool
from .config import UpstreamSpec

logger = logging.getLogger(__name__)


class UnknownUpstreamError(KeyError):
    """A call addressed to a server that is not in the pool."""


class UpstreamSession(Protocol):
    async def list_tools(self) -> list[Any]: ...
    async def call_tool(self, name: str, arguments: dict) -> Any: ...
    async def aclose(self) -> None: ...


SessionFactory = Callable[[UpstreamSpec], Awaitable[UpstreamSession]]


async def _default_session_factory(spec: UpstreamSpec) -> UpstreamSession:
    """Real transport, chosen by spec. Imported lazily so tests skip the mcp package."""
    if spec.transport == "http":
        from .http_session import HttpUpstreamSession

        session: UpstreamSession = HttpUpstreamSession(spec)
    else:
        from .stdio_session import StdioUpstreamSession

        session = StdioUpstreamSession(spec)

    await session.start()  # type: ignore[attr-defined]
    return session


class UpstreamPool:
    def __init__(
        self,
        specs: Iterable[UpstreamSpec],
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._specs = list(specs)
        self._factory = session_factory or _default_session_factory
        self._sessions: dict[str, UpstreamSession] = {}
        self._catalog: Catalog | None = None
        self._catalog_hash: str = ""

    async def start(self) -> None:
        for spec in self._specs:
            try:
                self._sessions[spec.server_id] = await self._factory(spec)
            except Exception:
                logger.exception("upstream %s failed to start", spec.server_id)

    async def aggregate(self) -> Catalog:
        tools: list[Tool] = []
        for server_id, session in self._sessions.items():
            try:
                advertised = await session.list_tools()
            except Exception:
                logger.exception("upstream %s failed tools/list", server_id)
                continue
            for raw in advertised:
                tools.append(
                    Tool(
                        server_id=server_id,
                        name=raw.name,
                        description=raw.description or "",
                        # mcp 2.x names this ``input_schema``; 1.x used ``inputSchema``.
                        input_schema=dict(getattr(raw, "input_schema", None) or {}),
                    )
                )

        self._catalog = Catalog(tools)
        self._catalog_hash = hashlib.sha256(
            "\n".join(sorted(t.uid for t in self._catalog)).encode()
        ).hexdigest()[:16]
        return self._catalog

    def catalog_hash(self) -> str:
        return self._catalog_hash

    async def call(self, server_id: str, tool_name: str, arguments: dict) -> Any:
        session = self._sessions.get(server_id)
        if session is None:
            raise UnknownUpstreamError(f"no upstream named {server_id!r}")
        return await session.call_tool(tool_name, arguments)

    async def aclose(self) -> None:
        for server_id, session in self._sessions.items():
            try:
                await session.aclose()
            except Exception:
                logger.exception("upstream %s failed to close", server_id)
        self._sessions.clear()
