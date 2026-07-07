from fastapi import HTTPException
from typing import Optional

from . import db


RESOURCE_MODULES = {
    "alert.realtime_summary": "info_summary",
    "alert.settings": "risk_alert",
    "alert.notifications": "risk_alert",
    "data_visualization.display": "data_visualization_chart",
    "data_visualization.data": "data_visualization_data",
    "data_visualization.integration": "data_visualization_integration",
    "data_visualization.integrated_points": "data_visualization_integration",
    "order_finance.records": "order_finance_progress",
    "sh_junneng.trades": "sh_junneng",
    "mid_event.monitor": "mid_event_monitor",
    "users": "user_management",
    "permissions": "user_management",
    "operation_logs": "user_management",
    "monitoring.status": "user_management",
}

GUEST_PERMISSIONS = {
    ("alert.realtime_summary", "view"),
    ("data_visualization.display", "view"),
}

VIEW_ACTIONS = {"view", "detail"}
EDIT_ACTIONS = {"create", "edit", "delete", "import"}


def is_admin(user: dict) -> bool:
    return user.get("role") in {"管理员", "admin"}


def is_guest(user: dict) -> bool:
    return user.get("role") == "guest" or bool(user.get("is_guest"))


def _module_permission(user: dict, module_code: str) -> Optional[dict]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """
            SELECT can_view, can_edit
            FROM module_permissions
            WHERE user_id = ? AND module_code = ?
            """,
            (user["id"], module_code),
        ).fetchone()
    return dict(row) if row else None


def can(user: dict, resource: str, action: str, context: Optional[dict] = None) -> bool:
    if not user:
        return False
    if is_admin(user):
        return True
    if is_guest(user):
        return (resource, action) in GUEST_PERMISSIONS

    module_code = RESOURCE_MODULES.get(resource, resource)
    permission = _module_permission(user, module_code)
    if not permission:
        return False
    if action in VIEW_ACTIONS:
        return bool(permission.get("can_view"))
    if action == "export":
        return bool(permission.get("can_edit"))
    if action in EDIT_ACTIONS or action == "manage":
        return bool(permission.get("can_edit"))
    return False


def require_permission(user: dict, resource: str, action: str, context: Optional[dict] = None) -> None:
    if not can(user, resource, action, context):
        raise HTTPException(status_code=403, detail="没有访问权限")


def get_user_permissions(user: dict) -> list[str]:
    if is_admin(user):
        return ["*:*"]
    if is_guest(user):
        return sorted(f"{resource}:{action}" for resource, action in GUEST_PERMISSIONS)

    permissions: list[str] = []
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            """
            SELECT module_code, can_view, can_edit
            FROM module_permissions
            WHERE user_id = ?
            """,
            (user["id"],),
        ).fetchall()
    for row in rows:
        module_code = row["module_code"]
        resources = [key for key, value in RESOURCE_MODULES.items() if value == module_code] or [module_code]
        for resource in resources:
            if row["can_view"]:
                permissions.append(f"{resource}:view")
                permissions.append(f"{resource}:detail")
            if row["can_edit"]:
                permissions.extend(
                    f"{resource}:{action}"
                    for action in ("create", "edit", "delete", "import", "export")
                )
    return sorted(set(permissions))


def get_data_scope_filter(user: dict, resource: str) -> dict:
    return {"scope": "all", "where": "", "params": []}
