"""A minimal real MCP server, used as a subprocess in transport tests.

Written against ``mcp`` 2.x, where ``MCPServer`` replaced FastMCP and the 1.x
``@server.list_tools()`` decorators no longer exist.
"""

import asyncio

from mcp.server.mcpserver import MCPServer

app = MCPServer("echo")


@app.tool(description="Echo the message back.")
def echo(message: str) -> str:
    return message


if __name__ == "__main__":
    asyncio.run(app.run_stdio_async())
