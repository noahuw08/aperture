"""One upstream MCP server, spoken to over streamable HTTP.

Hosted MCP endpoints — `github` and `notion` on this machine — are not subprocesses.
Headers carry their auth, so they go on a pre-configured httpx client rather than into
the transport call.

Lifetime and the anyio cancel-scope constraint are handled by ``RunnerSession``; this
module only supplies the transport.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from .config import UpstreamSpec
from .runner import RunnerSession


class HttpUpstreamSession(RunnerSession):
    def __init__(self, spec: UpstreamSpec) -> None:
        super().__init__(spec.server_id)
        self._spec = spec

    def _transport(self) -> AbstractAsyncContextManager[Any]:
        spec = self._spec

        @asynccontextmanager
        async def _connect():
            # The http client is closed with the transport, so it is entered inside
            # the same frame — see RunnerSession for why that matters.
            async with create_mcp_http_client(headers=spec.headers or None) as client:
                async with streamable_http_client(spec.url, http_client=client) as streams:
                    yield streams

        return _connect()
