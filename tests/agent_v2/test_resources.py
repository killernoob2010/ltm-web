from app.trading_agent.resources import ResourceGuard, ResourceSnapshot


def test_resource_guard_fails_closed_when_container_metrics_are_unavailable():
    guard = ResourceGuard(lambda: ResourceSnapshot(memory_fraction=None, cpu_fraction=None, source="missing"))

    decision = guard.admit()

    assert decision.allowed is False
    assert decision.reason == "metrics_unavailable"


def test_resource_guard_pauses_on_pressure_and_resumes_after_stability():
    snapshots = iter([
        ResourceSnapshot(memory_fraction=0.80, cpu_fraction=0.10, source="test"),
        ResourceSnapshot(memory_fraction=0.60, cpu_fraction=0.10, source="test"),
        ResourceSnapshot(memory_fraction=0.60, cpu_fraction=0.10, source="test"),
    ])
    times = iter([0.0, 61.0])
    guard = ResourceGuard(lambda: next(snapshots), clock=lambda: next(times), stable_seconds=60)

    assert guard.admit().allowed is False
    assert guard.admit().allowed is False
    assert guard.admit().allowed is True
