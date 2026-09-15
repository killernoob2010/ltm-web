"""One atomic, task-scoped budget shared by model, tool and search calls."""
from __future__ import annotations

import threading
import time
from typing import Any


class BudgetExceeded(RuntimeError):
    """A request cannot be admitted without exceeding the task budget."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


class RuntimeBudget:
    """Keep all counters together so a search cannot partially reserve."""

    _KINDS = frozenset({"model", "tool", "search"})

    def __init__(self, limits: Any, *, clock=time.monotonic, started_at: float | None = None):
        self.limits = limits
        self._clock = clock
        self._started_at = clock() if started_at is None else float(started_at)
        self._counts = {"model": 0, "tool": 0, "search": 0}
        self._cancelled = False
        self._lock = threading.Lock()

    @property
    def counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    @property
    def model_calls(self) -> int:
        return self.counters["model"]

    @property
    def tool_calls(self) -> int:
        return self.counters["tool"]

    @property
    def search_calls(self) -> int:
        return self.counters["search"]

    @property
    def max_models(self) -> int:
        return int(self.limits.max_models)

    @property
    def max_tools(self) -> int:
        return int(self.limits.max_tools)

    @property
    def max_search(self) -> int:
        return int(self.limits.max_search)

    def _remaining_seconds_unlocked(self) -> float:
        return max(0.0, float(self.limits.deadline_seconds) - (self._clock() - self._started_at))

    def remaining_seconds(self) -> float:
        with self._lock:
            return self._remaining_seconds_unlocked()

    def remaining_models(self) -> int:
        with self._lock:
            return max(0, self.max_models - self._counts["model"])

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def reserve(self, kind: str) -> None:
        """Atomically admit one operation; failed admissions change no counter."""
        with self._lock:
            if kind not in self._KINDS:
                raise BudgetExceeded("budget_exhausted", "invalid budget kind")
            if self._cancelled:
                raise BudgetExceeded("cancelled", "task is cancelled")
            if self._remaining_seconds_unlocked() <= 0:
                raise BudgetExceeded("deadline_exceeded", "task deadline exceeded")

            tool_increment = 1 if kind in {"tool", "search"} else 0
            search_increment = 1 if kind == "search" else 0
            if self._counts["model"] + (kind == "model") > self.max_models:
                raise BudgetExceeded("budget_exhausted", "model budget exceeded")
            if self._counts["tool"] + tool_increment > self.max_tools:
                raise BudgetExceeded("budget_exhausted", "tool budget exceeded")
            if self._counts["search"] + search_increment > self.max_search:
                raise BudgetExceeded("budget_exhausted", "search budget exceeded")

            self._counts["model"] += int(kind == "model")
            self._counts["tool"] += tool_increment
            self._counts["search"] += search_increment


__all__ = ["BudgetExceeded", "RuntimeBudget"]
