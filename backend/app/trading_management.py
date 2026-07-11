"""交易管理模块。

P0 只读事实、业务归类和业务开平关系均通过本独立路由扩展。
"""
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from . import db


router = APIRouter()

TRADING_MODULES = {
    "overview": "trading_overview",
    "positions": "trading_positions",
    "junneng": "trading_sh_junneng",
    "options": "trading_options",
    "export": "trading_export",
}


async def trading_management_current_user(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
