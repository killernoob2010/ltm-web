"""Admin-only HTTP boundary for Agent runtime and evaluation visibility."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from ..permissions import can, is_admin
from ..trading_management import trading_management_current_user
from . import quality
from .contracts import StrictModel


router = APIRouter(prefix="/admin/agent-quality")


class FeedbackIn(StrictModel):
    label: Literal["correct", "incorrect", "needs_review"]
    note: str = Field(default="", max_length=quality.MAX_NOTE)
    evaluator_version: str = Field(default=quality.QUALITY_VERSION, max_length=80)


def _require_admin(user: dict) -> None:
    if not is_admin(user) or not can(user, "agent_quality", "view"):
        raise HTTPException(status_code=403, detail="仅管理员可查看 Agent 运行与质量")


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/summary")
def summary(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    user: dict = Depends(trading_management_current_user),
):
    _require_admin(user)
    try:
        return quality.build_summary(start_date, end_date)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/runs")
def runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    state: str | None = Query(default=None, max_length=32),
    module: str | None = Query(default=None, max_length=64),
    user: dict = Depends(trading_management_current_user),
):
    _require_admin(user)
    try:
        return quality.list_runs(
            page=page,
            page_size=page_size,
            start_date=start_date,
            end_date=end_date,
            state=state,
            module=module,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/runs/{task_id}")
def run_detail(task_id: int, user: dict = Depends(trading_management_current_user)):
    _require_admin(user)
    detail = quality.get_run_detail(task_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Agent任务不存在")
    return detail


@router.post("/runs/{task_id}/feedback")
def run_feedback(task_id: int, payload: FeedbackIn, user: dict = Depends(trading_management_current_user)):
    _require_admin(user)
    try:
        feedback = quality.record_feedback(
            evaluator_id=int(user["id"]),
            task_id=task_id,
            label=payload.label,
            note=payload.note,
            evaluator_version=payload.evaluator_version,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"task_id": task_id, "feedback": feedback}


@router.get("/evaluations")
def evaluations(user: dict = Depends(trading_management_current_user)):
    _require_admin(user)
    return {"version": quality.QUALITY_VERSION, **quality.evaluation_catalog()}


@router.get("/evaluations/{batch_id}")
def evaluation_detail(batch_id: str, user: dict = Depends(trading_management_current_user)):
    _require_admin(user)
    try:
        return {"version": quality.QUALITY_VERSION, **quality.evaluation_batch(batch_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
