"""Loopback-only authenticated MCP server for the Agent worker."""
import contextvars
import ipaddress
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.types import Receive, Scope, Send

from . import tools
from .contracts import Principal
from .store import resolve_grant

_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("agent_v2_principal", default=None)


def _loopback_host(value: bytes | str) -> bool:
    """Accept only localhost/loopback Host values, including [::1]:port."""
    raw = value.decode("latin1") if isinstance(value, bytes) else str(value or "")
    raw = raw.strip().lower()
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0 or (raw[closing + 1:] and not raw[closing + 1:].startswith(":")):
            return False
        host = raw[1:closing]
    elif raw.count(":") == 1:
        host = raw.rsplit(":", 1)[0]
    else:
        host = raw
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return str(address) in {"127.0.0.1", "::1"}


class GrantMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        if not _loopback_host(headers.get(b"host", b"")):
            await _error(send, 403, "Host 不被允许")
            return
        auth = headers.get(b"authorization", b"").decode("latin1")
        if not auth.startswith("Bearer "):
            await _error(send, 401, "缺少执行凭证")
            return
        try:
            principal = resolve_grant(auth[7:].strip())
        except Exception:
            await _error(send, 401, "执行凭证无效")
            return
        origin = headers.get(b"origin")
        if origin and origin.decode("latin1") not in {"http://127.0.0.1", "http://localhost"} and not origin.decode("latin1").startswith(("http://127.0.0.1:", "http://localhost:")):
            await _error(send, 403, "Origin 不被允许")
            return
        token = _principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _principal.reset(token)


async def _error(send: Send, status: int, text: str):
    body = ("{\"error\":\"" + text + "\"}").encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _response(name: str, args: dict[str, Any]):
    principal = _principal.get()
    if principal is None:
        raise PermissionError("缺少执行上下文")
    envelope = tools.dispatch(principal, name, args)
    return tools.ToolResponse(status=envelope.status, data=envelope.model_dump(mode="json"))


def build_mcp_server() -> MCPServer:
    server = MCPServer("hongyuan-trading-agent-v2", version="2.0")

    def register(name: str, fn):
        server.add_tool(fn, name=name, description=tools.TOOL_SPECS[name]["description"],
                        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
                        structured_output=True)

    def describe_capabilities() -> tools.ToolResponse:
        return _response("describe_capabilities", {})

    def query_trade_facts(start_date: str, end_date: str, asset_type: str = "all", contracts: list[str] | None = None,
                          direction: str = "all", classification: str = "all") -> tools.ToolResponse:
        return _response("query_trade_facts", {**locals(), "contracts": contracts or []})

    def query_close_facts(start_date: str, end_date: str, asset_type: str = "all", contracts: list[str] | None = None,
                          direction: str = "all", classification: str = "all") -> tools.ToolResponse:
        return _response("query_close_facts", {**locals(), "contracts": contracts or []})

    def query_positions(as_of_mode: Literal["latest", "settlement_date"] = "latest", as_of_date: str | None = None,
                        asset_type: Literal["all", "future", "option"] = "all", contracts: list[str] | None = None,
                        direction: Literal["all", "buy", "sell"] = "all",
                        classification: Literal["all", "unclassified", "classified"] = "all") -> tools.ToolResponse:
        return _response("query_positions", {**locals(), "contracts": contracts or []})

    def summarize_positions(result_ref: str, group_by: list[str], metrics: list[str], order_by: str | None = None,
                            descending: bool = True) -> tools.ToolResponse:
        return _response("summarize_positions", locals())

    def summarize_facts(result_ref: str, group_by: list[str], metrics: list[str], order_by: str | None = None,
                        descending: bool = True) -> tools.ToolResponse:
        return _response("summarize_facts", locals())

    def read_result_page(result_ref: str, page: int, page_size: int = 20, fields: list[str] | None = None) -> tools.ToolResponse:
        return _response("read_result_page", {**locals(), "fields": fields or []})

    def compare_results(left_ref: str, right_ref: str, metrics: list[str]) -> tools.ToolResponse:
        return _response("compare_results", locals())

    def get_position_risk(snapshot_ref: str, include_futures: bool = False) -> tools.ToolResponse:
        return _response("get_position_risk", locals())

    def run_scenario(snapshot_ref: str, shocks: list[tools.Shock], method: str = "black76_reprice_v1") -> tools.ToolResponse:
        return _response("run_scenario", locals())

    def explain_evidence(result_ref: str, metric_path: str) -> tools.ToolResponse:
        return _response("explain_evidence", locals())

    def search_public(public_query: str, freshness: str = "none") -> tools.ToolResponse:
        return _response("search_public", locals())

    def read_public(source_ref: str) -> tools.ToolResponse:
        return _response("read_public", locals())

    for name, fn in (("describe_capabilities", describe_capabilities), ("query_trade_facts", query_trade_facts),
                     ("query_close_facts", query_close_facts), ("query_positions", query_positions),
                     ("summarize_positions", summarize_positions), ("summarize_facts", summarize_facts),
                     ("read_result_page", read_result_page), ("compare_results", compare_results),
                     ("get_position_risk", get_position_risk), ("run_scenario", run_scenario),
                     ("explain_evidence", explain_evidence), ("search_public", search_public), ("read_public", read_public)):
        register(name, fn)
    return server


def build_mcp_app():
    server = build_mcp_server()
    app = server.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True,
                                     host="127.0.0.1")
    return GrantMiddleware(app)
