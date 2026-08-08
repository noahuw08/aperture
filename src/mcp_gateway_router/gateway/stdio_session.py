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
        params = StdioServerParameters(
            command=self._spec.command,
            args=list(self._spec.args),
            env={**os.environ, **self._spec.env} if self._spec.env else None,
        )
        return stdio_client(params)
