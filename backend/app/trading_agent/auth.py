"""Re-check live application permissions at every tool boundary."""
import os
from uuid import UUID

from fastapi import HTTPException

from .. import db, permissions
from ..trading_effective_facts import EffectiveFactFilters
from .contracts import Principal


def require_account_scope(filters: EffectiveFactFilters) -> tuple[int, ...]:
    if not filters.account_ids:
        raise PermissionError("无可查询账户")
    return filters.account_ids


def pilot_allows_user(user: dict) -> bool:
    """Optional rollout restriction; application permissions remain mandatory."""
    pilot = os.environ.get("AGENT_V2_PILOT_USERNAME", "").strip()
    return not pilot or str(user.get("username") or "") == pilot


def _live_user(user_id: int) -> dict:
    with db.connect() as conn:
        rows = db._exec(conn.cursor(),
            "SELECT id, username, role, status FROM users WHERE id = ? AND status = '启用'",
            (user_id,)).fetchall()
    if len(rows) != 1 or not pilot_allows_user(dict(rows[0])):
        raise HTTPException(403, "没有访问权限")
    return dict(rows[0])


def _accounts(user: dict) -> tuple[int, ...]:
    # A data-visualization-only principal is valid and deliberately carries no
    # trading account scope.  An empty tuple must never mean "all accounts".
    if not permissions.can(user, "trading.facts", "view"):
        return ()
    scope = permissions.get_data_scope_filter(user, "trading.facts")
    # Fail closed if the application introduces a scope format this adapter cannot interpret.
    if scope.get("scope") != "all" or scope.get("where") or scope.get("params"):
        raise HTTPException(503, "账户权限范围需要适配")
    with db.connect() as conn:
        rows = db._exec(conn.cursor(),
            "SELECT id FROM trading_accounts WHERE account_code = ? AND is_active = 1",
            ("hongyuan_futures",)).fetchall()
    if len(rows) != 1:
        raise HTTPException(503, "宏源账户范围不可用")
    return (int(rows[0]["id"]),)


def resolve_principal(user_id: int, channel: str, conversation_id: int, execution_id: UUID) -> Principal:
    user = _live_user(user_id)
    permissions.require_permission(user, "closing_review.agent", "view")
    with db.connect() as conn:
        row = db._exec(conn.cursor(),
            "SELECT id FROM closing_review_conversations WHERE id = ? AND user_id = ? AND channel = ? AND kind = 'v2_conversation' AND status = 'active'",
            (conversation_id, user_id, channel)).fetchone()
    if not row or channel not in {"web", "wecom"}:
        raise HTTPException(404, "会话不存在")
    return Principal(user_id, channel, conversation_id, _accounts(user), UUID(str(execution_id)))


def authorize(principal: Principal, resource: str) -> Principal:
    if resource not in {
        "closing_review.agent",
        "trading.facts",
        "data_visualization.display",
        "data_visualization.data",
    }:
        raise HTTPException(403, "没有访问权限")
    current = resolve_principal(principal.user_id, principal.channel, principal.conversation_id, principal.execution_id)
    permissions.require_permission(_live_user(current.user_id), resource, "view")
    if current.account_ids != principal.account_ids:
        raise HTTPException(403, "账户范围已变化，请重新提问")
    return current
