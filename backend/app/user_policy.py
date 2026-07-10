def temporary_password_policy(name: str, username: str, department: str, role: str) -> dict[str, str]:
    if name.strip() == "曹骧":
        return {"temporary_password": username, "password_rule": "cao_xiang_exception"}
    if role == "领导":
        return {"temporary_password": f"{username}12345", "password_rule": "leader_12345"}
    if role == "用户" and department in {"贸易处", "期货组"}:
        return {"temporary_password": username, "password_rule": "trade_or_futures_plain"}
    if role == "用户" and department in {"财企处", "资金处"}:
        return {"temporary_password": f"{username}123", "password_rule": "finance_or_treasury_123"}
    return {"temporary_password": f"{username}123", "password_rule": "compatibility_fallback_123"}
