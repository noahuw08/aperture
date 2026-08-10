"""One upstream MCP server, spoken to over stdio.

Lifetime and the anyio cancel-scope constraint are handled by ``RunnerSession``; this
module only supplies the transport.
"""

from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from typing import Any

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import UpstreamSpec
from .runner import RunnerSession


class StdioUpstreamSession(RunnerSession):
    def __init__(self, spec: UpstreamSpec) -> None:
        super().__init__(spec.server_id)
        self._spec = spec

    def _transport(self) -> AbstractAsyncContextManager[Any]:
        # ⚠️ `env=None` is NOT "inherit the environment". The MCP SDK substitutes a
        # sanitised default carrying only HOME / LOGNAME / PATH / SHELL / TERM / USER,
        # so anything else is silently dropped.
        #
        # That default is the right behaviour for upstreams — playwright has no business
        # seeing NOTION_TOKEN — so a spec declares exactly what it needs and gets that
        # plus the parent environment. But it means launching *the gateway itself* through
        # this class requires putting MCP_GATEWAY_CONFIG / MCP_GATEWAY_TASK in `spec.env`;
        # exporting them in the parent shell is not enough and fails silently, serving the
        # default config as though nothing were wrong.
        params = StdioServerParameters(
            command=self._spec.command,
            args=list(self._spec.args),
            env={**os.environ, **self._spec.env} if self._spec.env else None,
        )
        return stdio_client(params)
