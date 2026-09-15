"""Strict contracts for planning a bounded, multi-source Agent task."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import StrictModel


class AnalysisTarget(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    domain: Literal["positions", "dataset", "public", "knowledge"]
    filters: dict[str, Any] = Field(default_factory=dict)
    metrics: list[str] = Field(default_factory=list, max_length=16)
    group_by: list[str] = Field(default_factory=list, max_length=8)
    net_intent: Literal["none", "net_buy", "net_sell", "net"] = "none"
    label: str = Field(min_length=1, max_length=120)


class TimeWindow(StrictModel):
    start_date: date
    end_date: date
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    origin: Literal["user", "default", "inherited"]

    @model_validator(mode="after")
    def ordered(self):
        if self.start_date > self.end_date:
            raise ValueError("时间范围开始日期不能晚于结束日期")
        return self


class AnalysisSpec(StrictModel):
    """Server-reviewed operation needed for deterministic analysis."""

    operation: Literal["lookup", "compare", "rank", "explain"]
    metric: str = Field(min_length=1, max_length=80)
    comparison_basis: Literal["none", "previous_period", "year_over_year", "explicit"]
    ranking_measure: Literal["value", "delta", "pct_change", "abs_delta"] | None = None
    descending: bool = False
    top_k: int | None = Field(default=None, ge=1, le=100)
    conclusion_required: bool = False


class Requirement(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=400)
    targets: list[AnalysisTarget] = Field(min_length=1, max_length=8)
    source_intent: Literal["internal", "public", "both", "knowledge", "discover"]
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    needs_full_text: bool = False
    time_requirement: str = Field(default="", max_length=160)
    time_window: TimeWindow | None = None
    analysis: AnalysisSpec | None = None


class ConditionOrigin(StrictModel):
    target_id: str = Field(min_length=1, max_length=40)
    field: str = Field(min_length=1, max_length=60)
    origin: Literal["current", "inherited", "default"]
    evidence: str = Field(min_length=1, max_length=160)


class UserRestriction(StrictModel):
    kind: Literal["no_web"]
    scope: Literal["turn", "conversation"]
    evidence: str = Field(min_length=1, max_length=160)


class RequirementCoverage(StrictModel):
    requirement_id: str = Field(min_length=1, max_length=40)
    status: Literal["answered", "partial", "blocked", "needs_clarification"]
    result_refs: list[str] = Field(default_factory=list, max_length=8)
    missing_codes: list[str] = Field(default_factory=list, max_length=16)
    delivery_blocks: list[str] = Field(default_factory=list, max_length=16)
    actual_scope: dict[str, Any] = Field(default_factory=dict)
    time_status: str = Field(default="unknown", max_length=80)


class CoverageReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[RequirementCoverage] = Field(default_factory=list, max_length=8)
    complete: bool = False

    @model_validator(mode="after")
    def validate_items(self):
        ids = [item.requirement_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("覆盖报告的需求 id 不能重复")
        if self.complete and not self.items:
            raise ValueError("覆盖报告不能为空")
        if self.complete and any(item.status != "answered" for item in self.items):
            raise ValueError("只有所有需求均已回答时覆盖报告才能完整")
        if self.complete and any(item.missing_codes or item.delivery_blocks for item in self.items):
            raise ValueError("完整覆盖不能包含未解决缺口或交付阻断")
        return self


class ConversationState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    topic: str = Field(default="", max_length=240)
    targets: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    condition_origins: list[ConditionOrigin] = Field(default_factory=list, max_length=32)
    restrictions: list[UserRestriction] = Field(default_factory=list, max_length=8)
    presentation: Literal["auto", "text", "table", "chart"] = "auto"
    result_refs: list[str] = Field(default_factory=list, max_length=8)
    open_requirements: list[str] = Field(default_factory=list, max_length=8)
    source_task_id: int | None = Field(default=None, ge=1)


class TaskPlan(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    objective: str = Field(min_length=1, max_length=500)
    topic_action: Literal["continue", "refine", "presentation_only", "refresh", "new_topic"]
    requirements: list[Requirement] = Field(min_length=1, max_length=8)
    condition_origins: list[ConditionOrigin] = Field(default_factory=list, max_length=32)
    restrictions: list[UserRestriction] = Field(default_factory=list, max_length=8)
    presentation: Literal["auto", "text", "table", "chart"] = "auto"
    prohibited_presentations: list[Literal["table", "chart"]] = Field(default_factory=list, max_length=2)
    clarification: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_graph_and_ids(self):
        requirement_ids = [item.id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("任务需求 id 不能重复")
        target_ids = [target.id for item in self.requirements for target in item.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("分析目标 id 不能重复")
        known = set(requirement_ids)
        for item in self.requirements:
            if any(dep not in known for dep in item.depends_on):
                raise ValueError("任务依赖必须引用已存在的需求")
        graph = {item.id: set(item.depends_on) for item in self.requirements}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str):
            if node in visiting:
                raise ValueError("任务需求依赖不能成环")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for requirement_id in graph:
            visit(requirement_id)
        origin_target_ids = {target.id for item in self.requirements for target in item.targets}
        if any(item.target_id not in origin_target_ids for item in self.condition_origins):
            raise ValueError("条件来源必须对应分析目标")
        if self.presentation in self.prohibited_presentations:
            raise ValueError("交付方式不能同时被禁止")
        return self
