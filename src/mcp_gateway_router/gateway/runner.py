"""Shared lifetime management for an upstream session.

**Why sessions live in a dedicated task.** ``stdio_client``, ``streamable_http_client``
and ``ClientSession`` each open an anyio task group internally, and anyio requires a
cancel scope to be exited by the task that entered it. Holding those contexts on an
``AsyncExitStack`` entered in ``start()`` and unwound in ``aclose()`` does not satisfy
that — the failure surfaces as ``RuntimeError: Attempted to exit cancel scope in a
different task than it was entered in``.

So the contexts live inside ``_run``, which enters them, publishes the ready session,
then parks on a shutdown event. One task, one frame, whole lifetime. Calling
``call_tool`` from another task is fine: ``ClientSession`` communicates over memory
streams, which are task-agnostic. Only the *scopes* are pinned.

Both transports share this because the constraint is identical for both, and a
duplicated version of a subtlety like this one is a bug waiting to be reintroduced.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import Any

from mcp import ClientSession


class RunnerSession(ABC):
    """One upstream MCP session, owned by a background task."""

    def __init__(self, server_id: str) -> None:
        self._server_id = server_id
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None

    @abstractmethod
    def _transport(self) -> AbstractAsyncContextManager[Any]:
        """An async context manager yielding ``(read_stream, write_stream)``."""

    async def _run(self) -> None:
        try:
            async with self._transport() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 — surfaced to start()'s caller
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
                f"upstream {self._server_id!r} failed to start"
            ) from self._error

    def _require(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(f"upstream {self._server_id!r} is not started")
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
