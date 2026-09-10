"""Fixed loopback MCP client used by Harness; it cannot accept a model URL."""
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx2

from .contracts import ToolEnvelope
from .execution import TOOL_SECONDS


class MCPToolClient:
    def __init__(self, endpoint: str | None = None, *, request_timeout_seconds: float = TOOL_SECONDS):
        self.endpoint = endpoint or f"http://127.0.0.1:{os.environ.get('AGENT_V2_MCP_PORT', '8766')}/mcp"
        if not self.endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("MCP endpoint must be loopback")
        self._streams = None
        self._client = None
        self._http_client = None
        self._initialized = False
        self._request_timeout_seconds = request_timeout_seconds
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        try:
            # The SDK bounds individual requests. A transport read timeout would
            # cancel the shared session task group, including its caller.
            self._http_client = await self._stack.enter_async_context(httpx2.AsyncClient(
                timeout=httpx2.Timeout(15, read=None), follow_redirects=False, trust_env=False))
            self._streams = await self._stack.enter_async_context(
                streamable_http_client(self.endpoint, http_client=self._http_client))
            self._client = await self._stack.enter_async_context(ClientSession(
                self._streams[0], self._streams[1], read_timeout_seconds=self._request_timeout_seconds))
            return self
        except BaseException:
            await self._stack.aclose()
            raise

    async def __aexit__(self, exc_type, exc, tb):
        return await self._stack.__aexit__(exc_type, exc, tb)

    async def list_tools(self, grant: str | None = None):
        if grant is not None:
            self._set_grant(grant)
        if not self._initialized and grant is None:
            raise PermissionError("list_tools requires an execution grant")
        if not self._initialized:
            await self._client.initialize()
            self._initialized = True
        return await self._client.list_tools()

    def _set_grant(self, grant: str):
        if not isinstance(grant, str) or not grant:
            raise PermissionError("缺少执行凭证")
        if self._http_client is None or self._client is None:
            raise RuntimeError("MCP client is not connected")
        self._http_client.headers["Authorization"] = f"Bearer {grant}"

    async def call_tool(self, name: str, arguments: dict[str, Any], grant: str) -> ToolEnvelope:
        if name not in {item["name"] for item in tools.tool_schemas()}:
            raise ValueError("未知工具")
        self._set_grant(grant)
        if not self._initialized:
            await self._client.initialize()
            self._initialized = True
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            raise RuntimeError("MCP tool failed")
        data = result.structured_content or {}
        if not data and result.content:
            import json
            data = json.loads(result.content[0].text)
        return ToolEnvelope.model_validate(data.get("data", data))


from . import tools
