"""Installed SDK compatibility over an actual loopback HTTP connection."""
import asyncio
import socket

import pytest
import uvicorn
from mcp.server import MCPServer
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


@pytest.mark.asyncio
async def test_installed_sdk_negotiates_and_calls_structured_tool():
    mcp = MCPServer("synthetic-runtime-probe")

    @mcp.tool(structured_output=True)
    def probe() -> dict[str, str]:
        return {"status": "complete", "quantity": "2"}

    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    async with mcp.session_manager.run():
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(.01)
                async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        initialized = await client.initialize()
                        assert initialized.protocol_version
                        listed = await client.list_tools()
                        assert any(tool.name == "probe" for tool in listed.tools)
                        result = await client.call_tool("probe", {})
                        assert result.structured_content == {"status": "complete", "quantity": "2"}
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
            listener.close()
