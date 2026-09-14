"""Business-facing view derived from the server-authorized tool catalog."""
from __future__ import annotations

from typing import Any


CATALOG_VERSION = "capability-catalog-v1"

_TOOL_METADATA: dict[str, dict[str, Any]] = {
    "query_positions": {
        "source_kind": "internal", "domains": ["positions"],
        "supports": ["持仓、期权/期货筛选、数量、净额、浮盈浮亏"],
    },
    "summarize_positions": {
        "source_kind": "internal", "domains": ["positions"],
        "supports": ["按登记维度汇总已取得的持仓结果"],
    },
    "query_dataset": {
        "source_kind": "internal", "domains": ["dataset"],
        "supports": ["已登记现货、库存、到港和期现数据"],
    },
    "compare_dataset": {
        "source_kind": "internal", "domains": ["dataset"],
        "supports": ["已登记数据集的比较和变化"],
    },
    "search_public": {
        "source_kind": "public", "domains": ["public"],
        "supports": ["公开新闻、公告、天气和其他公开资料检索"],
    },
    "read_public": {
        "source_kind": "public", "domains": ["public"],
        "supports": ["读取已登记的公开来源正文"],
    },
}


def _tool_name(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(getattr(item, "name", "") or "")


def build_catalog(
    principal_or_payload: Any = None,
    capability_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a descriptive catalog without adding tools or granting access."""
    # Accept both the documented (principal, payload) form and the compact
    # payload-only form used by offline callers.  The principal is deliberately
    # not interpreted here; its live permissions are already reflected by the
    # server-generated visible tool list.
    payload_input = capability_payload if capability_payload is not None else principal_or_payload
    payload = payload_input if isinstance(payload_input, dict) else {}
    visible = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    tools: list[dict[str, Any]] = []
    for item in visible:
        name = _tool_name(item)
        if not name:
            continue
        schema = item.get("inputSchema", {}) if isinstance(item, dict) else {}
        metadata = _TOOL_METADATA.get(name, {
            "source_kind": "internal", "domains": ["discover"], "supports": [],
        })
        tools.append({
            "id": name,
            "input_schema": schema if isinstance(schema, dict) else {},
            "source_kind": metadata["source_kind"],
            "domains": list(metadata["domains"]),
            "supports": list(metadata["supports"]),
            "coverage_status": "unknown",
            "coverage": {"status": "unknown", "latest": None, "scope": None},
        })
    return {
        "version": CATALOG_VERSION,
        "tools": tools,
        "conditional_sources": ["public_research"],
        "modules": payload.get("modules", {}),
        "research": payload.get("research_policy", {}),
    }
