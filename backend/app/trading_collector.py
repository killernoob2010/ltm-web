"""HTTP boundary for the read-only WH6 collector and its device controls."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import db
from . import trading_collector_service as service
from .permissions import is_admin, require_permission


router = APIRouter(prefix="/trading-collector", tags=["trading-collector"])


class PairingCodeIn(BaseModel):
    account_id: int = Field(gt=0)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class ActivateDeviceIn(BaseModel):
    pairing_code: str = Field(min_length=6, max_length=120)
    device_name: str = Field(min_length=1, max_length=120)
    client_version: str = Field(default="", max_length=40)
    fingerprint: str = Field(min_length=1, max_length=200)


class HeartbeatIn(BaseModel):
    client_version: Optional[str] = Field(default=None, max_length=40)


class IngestIn(BaseModel):
    observations: List[Dict[str, Any]] = Field(default_factory=list, max_length=500)
    position_snapshots: List[Dict[str, Any]] = Field(default_factory=list)


class ReconcileIn(BaseModel):
    account_id: int = Field(gt=0)
    start_date: str = Field(default="", max_length=10)
    end_date: str = Field(default="", max_length=10)


def _service_error(exc: service.CollectorServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def trading_collector_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def device_auth(x_collector_token: Optional[str] = Header(default=None, alias="X-Collector-Token")) -> dict:
    if not x_collector_token:
        raise HTTPException(status_code=401, detail={"code": "device_token_required", "message": "缺少采集器设备令牌"})
    try:
        return service.get_device_by_token(x_collector_token)
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/admin/pairing-codes")
def create_pairing_code(payload: PairingCodeIn, user=Depends(trading_collector_current_user)):
    require_permission(user, "trading.collector", "manage")
    try:
        return service.issue_pairing_code(payload.account_id, actor_id=user["id"], ttl_seconds=payload.ttl_seconds)
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/admin/devices")
def get_devices(
    account_id: Optional[int] = Query(default=None, gt=0),
    user=Depends(trading_collector_current_user),
):
    require_permission(user, "trading.collector", "manage")
    return {"items": service.list_devices(account_id)}


@router.get("/admin/collection-policy")
def get_admin_collection_policy(
    account_id: int = Query(..., gt=0),
    user=Depends(trading_collector_current_user),
):
    require_permission(user, "trading.collector", "manage")
    try:
        return service.get_account_collection_policy(account_id)
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/admin/reconcile")
def reconcile_admin_collection(payload: ReconcileIn, user=Depends(trading_collector_current_user)):
    require_permission(user, "trading.collector", "manage")
    try:
        return service.reconcile_intraday_range(
            payload.account_id,
            payload.start_date,
            payload.end_date,
            str(user.get("name") or user.get("username") or "trading_collector_admin"),
        )
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/admin/devices/{device_id}/revoke")
def revoke_device(device_id: int, user=Depends(trading_collector_current_user)):
    require_permission(user, "trading.collector", "manage")
    try:
        return service.revoke_device(device_id, actor_id=user["id"])
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/device/activate")
def activate_device(payload: ActivateDeviceIn):
    try:
        return service.activate_device(payload.pairing_code, payload.device_name, payload.client_version, payload.fingerprint)
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/device/heartbeat")
def heartbeat_device(payload: HeartbeatIn, device=Depends(device_auth)):
    # The dependency already validates the token; service updates only that device.
    try:
        return service.heartbeat_device_id(device["id"], payload.client_version)
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/device/collection-policy")
def get_collection_policy(device=Depends(device_auth)):
    try:
        return service.get_device_collection_policy(device["id"])
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/device/ingest")
def ingest(payload: IngestIn, device=Depends(device_auth), x_collector_token: Optional[str] = Header(default=None, alias="X-Collector-Token")):
    if not x_collector_token:
        raise HTTPException(status_code=401, detail={"code": "device_token_required", "message": "缺少采集器设备令牌"})
    try:
        return service.ingest_observations(
            x_collector_token,
            payload.observations,
            payload.position_snapshots,
        ).to_dict()
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/fills")
def get_fills(
    account_id: Optional[int] = Query(default=None, gt=0),
    start: str = Query(default="", max_length=10),
    end: str = Query(default="", max_length=10),
    contract: str = Query(default="", max_length=80),
    status: str = Query(default="active", max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user=Depends(trading_collector_current_user),
):
    require_permission(user, "trading.options", "view")
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures' AND is_active = 1").fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="宏源期货账户不存在")
    default_account = row["id"]
    if account_id not in (None, default_account):
        if not is_admin(user):
            raise HTTPException(status_code=403, detail="无权读取其他交易账户的盘中成交")
        raise HTTPException(status_code=400, detail="第一版采集器只提供宏源期货账户的盘中成交")
    target_account = account_id or default_account
    try:
        return service.query_intraday_fills(
            target_account,
            start=start,
            end=end,
            contract=contract,
            status=status,
            page=page,
            page_size=page_size,
            asset_type="option",
        )
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


def _hongyuan_account_id() -> int:
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures' AND is_active = 1",
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="宏源期货账户不存在")
    return int(row["id"])


def _read_target_account(account_id: Optional[int], user: dict) -> int:
    default_account = _hongyuan_account_id()
    if account_id not in (None, default_account):
        if not is_admin(user):
            raise HTTPException(status_code=403, detail="无权读取其他交易账户的盘中数据")
        raise HTTPException(status_code=400, detail="第一版采集器只提供宏源期货账户的盘中数据")
    return account_id or default_account


@router.get("/option-volume")
def get_option_volume(
    account_id: Optional[int] = Query(default=None, gt=0),
    trade_date: str = Query(default="", max_length=10),
    contract: str = Query(default="", max_length=80),
    user=Depends(trading_collector_current_user),
):
    require_permission(user, "trading.options", "view")
    target_account = _read_target_account(account_id, user)
    try:
        return service.query_option_volume(target_account, trade_date=trade_date, contract=contract)
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/positions/current")
def get_current_option_positions(
    account_id: Optional[int] = Query(default=None, gt=0),
    user=Depends(trading_collector_current_user),
):
    require_permission(user, "trading.options", "view")
    target_account = _read_target_account(account_id, user)
    try:
        return service.query_current_option_positions(target_account)
    except service.CollectorServiceError as exc:
        raise _service_error(exc) from exc
