"""One upstream MCP server, spoken to over stdio.

**Why this holds the session in a dedicated task rather than an ``AsyncExitStack``.**
``stdio_client`` and ``ClientSession`` each open an anyio task group internally, and
anyio requires a cancel scope to be exited by the same task that entered it. An exit
stack entered in ``start()`` and unwound in ``aclose()`` does not satisfy that even when
both run in the same task, because the stack closes the generators out of their original
nesting — the failure surfaces as ``RuntimeError: Attempted to exit cancel scope in a
different task than it was entered in``.

So the contexts live inside ``_run``, which enters them, publishes the ready session,
then parks on a shutdown event. The whole lifetime is one task and one frame. Calling
``call_tool`` from another task is fine: ``ClientSession`` communicates over memory
streams, which are task-agnostic. Only the *scopes* are pinned.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import UpstreamSpec


class StdioUpstreamSession:
    def __init__(self, spec: UpstreamSpec) -> None:
        self._spec = spec
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None

    async def _run(self) -> None:
        params = StdioServerParameters(
            command=self._spec.command,
            args=list(self._spec.args),
            env={**os.environ, **self._spec.env} if self._spec.env else None,
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 — re-raised to the caller of start()
            self._error = exc
        finally:
            self._session = None
            # Unblocks start() when the failure happened before the session was ready.
            self._ready.set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._error is not None:
            await self.aclose()
            raise RuntimeError(
                f"upstream {self._spec.server_id!r} failed to start"
            ) from self._error

    def _require(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(f"upstream {self._spec.server_id!r} is not started")
        return self._session

    async def list_tools(self) -> list[Any]:
        return (await self._require().list_tools()).tools

    async def call_tool(self, name: str, arguments: dict) -> Any:
        return await self._require().call_tool(name, arguments)

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            finally:
                self._task = None
