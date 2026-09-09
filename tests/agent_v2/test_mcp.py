import asyncio
import socket

import httpx2
import pytest
import uvicorn

from app.trading_agent import mcp_client, mcp_server, store
from test_store import queued


def test_mcp_accepts_bracketed_ipv6_loopback_host_only():
    assert mcp_server._loopback_host("[::1]:8766") is True
    assert mcp_server._loopback_host("[2001:db8::1]:8766") is False
    assert mcp_server._loopback_host("127.0.0.1:8766") is True
    assert mcp_server._loopback_host("127.0.0.2:8766") is False


@pytest.mark.asyncio
async def test_mcp_loopback_requires_live_grant_and_calls_describe(queued):
    task_id = store.claim_next("mcp-worker")
    principal = store.principal_for_task(task_id)
    grant = store.issue_grant(principal)
    app = mcp_server.build_mcp_app()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0)); listener.listen(8)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(.01)
            async with httpx2.AsyncClient(timeout=5, follow_redirects=False) as client:
                denied = await client.post(f"http://127.0.0.1:{port}/mcp", json={"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}})
                assert denied.status_code == 401
                response = await client.post(f"http://127.0.0.1:{port}/mcp", headers={"Authorization":f"Bearer {grant}","Accept":"application/json, text/event-stream"}, json={"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}})
                assert response.status_code == 200
                assert "query_positions" in response.text
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


@pytest.mark.asyncio
async def test_mcp_expired_grant_rejects_before_tool(queued):
    task_id = store.claim_next("mcp-worker")
    principal = store.principal_for_task(task_id)
    grant = store.issue_grant(principal)
    with __import__("app").db.connect() as conn:
        conn.execute("UPDATE agent_v2_execution_grants SET expires_at='2000-01-01T00:00:00+00:00' WHERE token_hash=?", (store.digest(grant),))
    app = mcp_server.build_mcp_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.post("/mcp", headers={"Authorization":f"Bearer {grant}"}, json={"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_sdk_client_lists_and_calls_only_with_grant(queued):
    task_id = store.claim_next("mcp-sdk-worker")
    principal = store.principal_for_task(task_id)
    grant = store.issue_grant(principal)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0)); listener.listen(8)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp_server.build_mcp_app(), log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(.01)
            async with mcp_client.MCPToolClient(f"http://127.0.0.1:{port}/mcp") as client:
                listed = await client.list_tools(grant)
                assert any(tool.name == "query_positions" for tool in listed.tools)
                envelope = await client.call_tool("describe_capabilities", {}, grant)
                assert envelope.status == "complete"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
