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
    "order_finance.capital": "order_finance_capital",
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
EDIT_ACTIONS = {"create", "edit"}
SENSITIVE_ACTIONS = {"delete", "import", "export", "manage"}
ADMIN_ONLY_RESOURCES = {"users", "permissions", "operation_logs", "monitoring.status"}

DEPARTMENTS = ("贸易处", "期货组", "财企处", "资金处", "管理部门")
USER_ROLES = ("用户", "领导", "管理员")
ACTIVE_BUSINESS_MODULES = {
    "sh_junneng",
    "info_summary",
    "risk_alert",
    "mid_event_monitor",
    "data_visualization_integration",
    "data_visualization_data",
    "data_visualization_chart",
    "order_finance_progress",
    "order_finance_capital",
}
DEPARTMENT_MODULES = {
    "贸易处": {
        "info_summary", "risk_alert", "mid_event_monitor",
        "data_visualization_integration", "data_visualization_data", "data_visualization_chart",
    },
    "期货组": {
        "sh_junneng", "info_summary", "risk_alert", "mid_event_monitor",
        "data_visualization_integration", "data_visualization_data", "data_visualization_chart",
    },
    "财企处": {
        "data_visualization_integration", "data_visualization_data", "data_visualization_chart",
        "order_finance_progress", "order_finance_capital",
    },
    "资金处": {
        "data_visualization_integration", "data_visualization_data", "data_visualization_chart",
        "order_finance_progress", "order_finance_capital",
    },
    "管理部门": set(ACTIVE_BUSINESS_MODULES),
}


def default_permission_levels(department: str, role: str) -> dict[str, str]:
    levels = {code: "none" for _, code, _ in db.MODULES}
    if role == "管理员":
        return {code: "sensitive" for _, code, _ in db.MODULES}
    if role == "领导":
        for code in ACTIVE_BUSINESS_MODULES:
            levels[code] = "view"
        return levels
    for code in DEPARTMENT_MODULES.get(department, set()):
        levels[code] = "operate"
    return levels


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
            SELECT can_view, can_edit, can_sensitive
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
    if resource in ADMIN_ONLY_RESOURCES:
        return False

    module_code = RESOURCE_MODULES.get(resource, resource)
    permission = _module_permission(user, module_code)
    if not permission:
        return False
    if action in VIEW_ACTIONS:
        return bool(permission.get("can_view"))
    if action in EDIT_ACTIONS:
        return bool(permission.get("can_edit"))
    if action in SENSITIVE_ACTIONS:
        return bool(permission.get("can_sensitive"))
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
            SELECT module_code, can_view, can_edit, can_sensitive
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
                    for action in ("create", "edit")
                )
            if row["can_sensitive"]:
                permissions.extend(
                    f"{resource}:{action}"
                    for action in ("delete", "import", "export", "manage")
                )
    return sorted(set(permissions))


def get_data_scope_filter(user: dict, resource: str) -> dict:
    return {"scope": "all", "where": "", "params": []}
