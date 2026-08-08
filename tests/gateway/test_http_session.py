"""Streamable-HTTP transport, against a real MCP server on a real socket.

The hosted upstreams this project actually proxies (`github`, `notion`) are HTTP, so
this path carries most of the catalog. Mocking the SDK here would test the mock.
"""

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_gateway_router.gateway.config import UpstreamSpec
from mcp_gateway_router.gateway.http_session import HttpUpstreamSession

SERVER = str(Path(__file__).parent / "http_echo_server.py")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_until_listening(port: int, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            await asyncio.sleep(0.1)
    raise RuntimeError(f"http echo server never listened on {port}")


@pytest.fixture
async def http_server():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, SERVER, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_until_listening(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_list_tools_over_http(http_server):
    spec = UpstreamSpec(server_id="echo", transport="http", url=http_server)
    session = HttpUpstreamSession(spec)
    await session.start()
    try:
        tools = await session.list_tools()
    finally:
        await session.aclose()

    assert [t.name for t in tools] == ["echo"]
    assert tools[0].input_schema["properties"]["message"]["type"] == "string"


async def test_call_tool_over_http(http_server):
    spec = UpstreamSpec(server_id="echo", transport="http", url=http_server)
    session = HttpUpstreamSession(spec)
    await session.start()
    try:
        result = await session.call_tool("echo", {"message": "hello"})
    finally:
        await session.aclose()

    assert result.content[0].text == "hello"


async def test_the_pool_picks_the_http_transport_from_the_spec(http_server):
    from mcp_gateway_router.gateway.upstream import UpstreamPool

    pool = UpstreamPool(
        [UpstreamSpec(server_id="echo", transport="http", url=http_server)]
    )
    await pool.start()
    try:
        catalog = await pool.aggregate()
    finally:
        await pool.aclose()

    assert len(catalog) == 1
    assert catalog.get("echo", "echo") is not None
