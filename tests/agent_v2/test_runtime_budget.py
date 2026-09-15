import pytest

from app.trading_agent.harness import RuntimeLimits
from app.trading_agent.runtime_budget import BudgetExceeded, RuntimeBudget


def test_search_reservation_checks_tool_and_search_atomically():
    budget = RuntimeBudget(
        RuntimeLimits(max_tools=1, max_search=2),
        clock=lambda: 10.0,
        started_at=10.0,
    )

    budget.reserve("search")
    assert budget.counters == {"model": 0, "tool": 1, "search": 1}

    with pytest.raises(BudgetExceeded) as error:
        budget.reserve("search")
    assert error.value.code == "budget_exhausted"
    assert budget.counters == {"model": 0, "tool": 1, "search": 1}


def test_cancel_prevents_next_request():
    budget = RuntimeBudget(RuntimeLimits(), clock=lambda: 10.0, started_at=10.0)
    budget.cancel()

    with pytest.raises(BudgetExceeded) as error:
        budget.reserve("model")

    assert error.value.code == "cancelled"
    assert budget.counters == {"model": 0, "tool": 0, "search": 0}


def test_deadline_and_invalid_kind_are_fail_closed():
    now = [10.0]
    budget = RuntimeBudget(
        RuntimeLimits(deadline_seconds=1.0),
        clock=lambda: now[0],
        started_at=10.0,
    )
    with pytest.raises(BudgetExceeded, match="kind"):
        budget.reserve("network")
    now[0] = 11.0
    with pytest.raises(BudgetExceeded) as error:
        budget.reserve("tool")
    assert error.value.code == "deadline_exceeded"


def test_model_limit_does_not_increment_after_rejection():
    budget = RuntimeBudget(
        RuntimeLimits(max_models=1), clock=lambda: 10.0, started_at=10.0
    )
    budget.reserve("model")
    with pytest.raises(BudgetExceeded):
        budget.reserve("model")
    assert budget.counters["model"] == 1
