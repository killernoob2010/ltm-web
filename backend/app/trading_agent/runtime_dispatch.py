"""Select and route one task to the legacy or Pydantic AI runtime."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from . import harness, pydantic_runtime


_VALID_BACKENDS = frozenset({"legacy", "pydantic"})


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _pilot_user_ids(value: Any) -> set[int]:
    if isinstance(value, set):
        values = value
    elif isinstance(value, (list, tuple, frozenset)):
        values = value
    else:
        values = str(value or "").replace("，", ",").split(",")
    result: set[int] = set()
    for item in values:
        if item in (None, ""):
            continue
        try:
            user_id = int(str(item).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_pilot_user_ids") from exc
        if user_id > 0:
            result.add(user_id)
        else:
            raise ValueError("invalid_pilot_user_ids")
    return result


def runtime_config() -> dict[str, Any]:
    """Read fail-closed runtime controls without exposing protected settings."""
    backend = os.environ.get("AGENT_V2_RUNTIME_BACKEND", "legacy").strip().lower()
    return {
        "backend": backend,
        "pilot_user_ids": _pilot_user_ids(os.environ.get("AGENT_V2_RUNTIME_PILOT_USER_IDS", "")),
        "disabled": _enabled(os.environ.get("AGENT_V2_PYDANTIC_DISABLED", "false")),
    }


def select_backend(user_id: Any, conversation_id: Any = None,
                   config: dict[str, Any] | None = None, *, bound_backend: str | None = None) -> str:
    """Choose one backend deterministically, with an optional conversation binding."""
    # Keep a compact two-argument form for local callers while preserving the
    # public contract's explicit conversation_id slot. The conversation id is
    # intentionally not interpreted by this pure selector; persistence owns it.
    if config is None and isinstance(conversation_id, dict):
        config = conversation_id
        conversation_id = None
    config = config or runtime_config()
    if bool(config.get("disabled")):
        return "legacy"
    backend = str(config.get("backend", "legacy") or "legacy").strip().lower()
    if backend not in _VALID_BACKENDS:
        raise ValueError("invalid_runtime_backend")
    if bound_backend is not None:
        bound = str(bound_backend).strip().lower()
        if bound not in _VALID_BACKENDS:
            raise ValueError("invalid_runtime_backend_binding")
        return bound
    if backend != "pydantic":
        return "legacy"
    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return "legacy"
    return "pydantic" if numeric_user_id in _pilot_user_ids(config.get("pilot_user_ids", set())) else "legacy"


async def run_task(task_id: int, deps):
    """Bind the session once, then delegate the complete task to one runtime."""
    principal = await asyncio.to_thread(deps.store.principal_for_task, task_id)
    bound = await asyncio.to_thread(deps.store.runtime_backend_for_task, task_id)
    config = runtime_config()
    selected = select_backend(
        principal.user_id, principal.conversation_id, config, bound_backend=bound,
    )
    if not config.get("disabled"):
        bound_result = await asyncio.to_thread(deps.store.bind_runtime, principal, selected)
        selected = str(bound_result or selected)
    if selected == "pydantic":
        return await pydantic_runtime.run_task(task_id, deps)
    return await harness.run_task(task_id, deps)


__all__ = ["run_task", "runtime_config", "select_backend"]
