import sys
from pathlib import Path

from mcp_gateway_router.gateway.config import UpstreamSpec
from mcp_gateway_router.gateway.stdio_session import StdioUpstreamSession

ECHO = str(Path(__file__).parent / "echo_server.py")


def _spec():
    return UpstreamSpec(server_id="echo", command=sys.executable, args=(ECHO,))


async def test_list_tools_reaches_a_real_server():
    session = StdioUpstreamSession(_spec())
    await session.start()
    try:
        tools = await session.list_tools()
    finally:
        await session.aclose()

    assert [t.name for t in tools] == ["echo"]
    assert tools[0].input_schema["properties"]["message"]["type"] == "string"


async def test_call_tool_round_trips():
    session = StdioUpstreamSession(_spec())
    await session.start()
    try:
        result = await session.call_tool("echo", {"message": "hello"})
    finally:
        await session.aclose()

    assert result.content[0].text == "hello"
