"""Bounded, server-persisted state for related Agent follow-ups."""
from __future__ import annotations

import json
import re
from typing import Any

from .planning_contracts import ConversationState, CoverageReport, TaskPlan


def extract_conversation_state(history: Any) -> dict[str, Any]:
    """Extract only the latest validated state embedded by the store."""
    for item in reversed(list(history or [])):
        content = item.get("content") if isinstance(item, dict) else ""
        match = re.search(r"conversation_state=(\{.*\})\s*$", str(content or ""), re.S)
        if not match:
            continue
        try:
            value = json.loads(match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            try:
                return ConversationState.model_validate(value).model_dump(mode="json")
            except ValueError:
                continue
    return {}


def update_conversation_state(
    previous: Any,
    plan: TaskPlan,
    coverage: CoverageReport,
    result_refs: list[str],
    *,
    source_task_id: int | None = None,
) -> ConversationState:
    """Create the next state; turn-only restrictions never persist."""
    old = previous if isinstance(previous, dict) else {}
    is_new_topic = plan.topic_action == "new_topic"
    old_refs = [] if is_new_topic else [str(value) for value in old.get("result_refs", []) if value]
    refs = list(dict.fromkeys([str(value) for value in result_refs if value] + old_refs))[:8]
    restrictions = [
        item.model_dump(mode="json")
        for item in plan.restrictions
        if item.scope == "conversation"
    ]
    open_requirements = [item.requirement_id for item in coverage.items if item.status != "answered"]
    targets = [
        target.model_dump(mode="json")
        for requirement in plan.requirements
        for target in requirement.targets
    ][:8]
    presentation = plan.presentation
    if presentation == "auto" and not is_new_topic:
        presentation = old.get("presentation", "auto") if old.get("presentation") in {"auto", "text", "table", "chart"} else "auto"
    return ConversationState(
        topic=plan.objective,
        targets=targets,
        condition_origins=plan.condition_origins,
        restrictions=restrictions,
        presentation=presentation,
        result_refs=refs,
        open_requirements=open_requirements,
        source_task_id=source_task_id,
    )


__all__ = ["extract_conversation_state", "update_conversation_state"]
