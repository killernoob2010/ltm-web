"""Server-owned decision policy for optional public research."""
from __future__ import annotations

import os
import re
from typing import Literal

from pydantic import Field

from .. import db
from .contracts import StrictModel


POLICY_VERSION = "research-policy-v1"


class RequestPlan(StrictModel):
    mode: Literal["internal_only", "research_allowed", "clarification_required"]
    reason: Literal[
        "internal_lookup", "internal_calculation", "registered_definition", "explicit_external",
        "external_current_fact", "mixed_research", "ambiguous",
    ]
    domains: list[Literal["trading", "spot", "basis", "public"]] = Field(default_factory=list, max_length=4)
    external_purpose: str = Field(default="", max_length=240)
    clarification: str | None = Field(default=None, max_length=240)


_NO_WEB = re.compile(r"不要(?:联网|上网|搜索)|不(?:要|用)外部资料|只(?:根据|用)内部|仅(?:根据|用)数据库|不要查网页", re.I)
_EXTERNAL = re.compile(
    r"联网|上网|网上|搜索|查(?:看|找|一下)?(?:新闻|政策|规则|公告|官网|资料)|"
    r"外部(?:信息|事件|资料)|近期(?:政策|新闻|规则|公告)|"
    r"交易所(?:近期|最新)?(?:规则|公告|政策)|官方(?:网站|来源|规则)",
    re.I,
)
_INTERNAL = re.compile(
    r"持仓|成交|平仓|盈亏|账户|库存|发运|到港|表需|基差|港差|现货|期现|环比|同比|趋势|快照|数据库|系统口径|登记口径|最新库存",
    re.I,
)
_AMBIGUOUS = re.compile(r"研究一下|分析一下|最新情况|最近怎么样|原因是什么|为什么变化", re.I)


def public_tools_configured() -> bool:
    return bool(os.environ.get("BRAVE_SEARCH_API_KEY", "").strip())


def public_tools_allowed(plan: RequestPlan | None, configured: bool) -> bool:
    return bool(plan and configured and plan.mode == "research_allowed")


def _plan(mode, reason, domains, *, purpose="", clarification=None):
    return RequestPlan(
        mode=mode, reason=reason, domains=list(dict.fromkeys(domains)),
        external_purpose=purpose[:240], clarification=clarification,
    )


def enforce_research_policy(user_text: str, candidate: RequestPlan) -> RequestPlan:
    """Apply deterministic, fail-closed rules to the current user message."""
    text = str(user_text or "").strip()
    if not isinstance(candidate, RequestPlan):
        candidate = RequestPlan.model_validate(candidate)
    no_web = bool(_NO_WEB.search(text))
    external = bool(_EXTERNAL.search(text))
    internal = bool(_INTERNAL.search(text))
    ambiguous = bool(_AMBIGUOUS.search(text))
    if no_web and external:
        return _plan(
            "clarification_required", "ambiguous", ["public"],
            clarification="这次请求同时要求联网和不联网。请明确是否允许查询公开资料；内部数据可先单独完成。",
        )
    if no_web:
        return _plan("internal_only", "internal_lookup" if internal else "ambiguous", ["trading", "spot", "basis"])
    if external:
        reason = "mixed_research" if internal else "external_current_fact"
        return _plan(
            "research_allowed", reason,
            ["trading", "spot", "basis", "public"] if internal else ["public"],
            purpose=text,
        )
    if internal:
        reason = "registered_definition" if re.search(r"口径|定义|公式|规则说明", text) else "internal_lookup"
        return _plan("internal_only", reason, ["trading", "spot", "basis"])
    if ambiguous:
        return _plan(
            "clarification_required", "ambiguous", ["public"],
            clarification="请说明是基于系统内部数据，还是需要查询公开资料；如果需要联网，请给出具体主题或来源范围。",
        )
    # Candidate model classification never gets to silently enable external
    # access for an otherwise unclear request.
    if candidate.mode == "internal_only":
        return _plan("internal_only", "internal_lookup", candidate.domains or ["trading"])
    return _plan(
        "clarification_required", "ambiguous", ["public"],
        clarification="请明确需要查询的内部数据或公开主题。",
    )


def active_plan(principal) -> RequestPlan | None:
    """Read the current run's server-recorded policy, never client arguments."""
    try:
        with db.connect() as conn:
            row = db._exec(conn.cursor(), """SELECT e.status, e.error_code, e.tool_name
                FROM agent_v2_events e JOIN agent_v2_runs r ON r.task_id=e.task_id
                WHERE r.execution_id=? AND r.user_id=? AND r.state='running' AND e.kind='research_policy'
                ORDER BY e.seq DESC LIMIT 1""", (str(principal.execution_id), principal.user_id)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        domains = [item for item in str(row["tool_name"] or "").split(",") if item]
        return RequestPlan(mode=row["status"], reason=row["error_code"], domains=domains)
    except Exception:
        return None


__all__ = [
    "POLICY_VERSION", "RequestPlan", "active_plan", "enforce_research_policy",
    "public_tools_allowed", "public_tools_configured",
]
