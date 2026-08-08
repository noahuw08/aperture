"""A minimal real MCP server over streamable HTTP, for transport tests.

Takes the port as argv[1] so the test can pick a free one.
"""

import asyncio
import sys

from mcp.server.mcpserver import MCPServer

app = MCPServer("http-echo")


@app.tool(description="Echo the message back.")
def echo(message: str) -> str:
    return message


if __name__ == "__main__":
    asyncio.run(app.run_streamable_http_async(host="127.0.0.1", port=int(sys.argv[1])))
