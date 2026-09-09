import sys

from app.trading_agent.runtime import RestartBudget, SupervisorConfig, child_commands


def test_agent_command_is_present_only_when_shared_runtime_is_enabled():
    disabled = SupervisorConfig(agent_enabled=False, web_port="8000")
    enabled = SupervisorConfig(agent_enabled=True, web_port="9000")

    assert child_commands(disabled)["web"][-1] == "8000"
    assert "agent" not in child_commands(disabled)
    assert child_commands(enabled)["web"][-1] == "9000"
    assert child_commands(enabled)["agent"] == [sys.executable, "-m", "backend.app.trading_agent.worker"]


def test_restart_budget_allows_three_failures_in_a_window_then_stops():
    clock = iter([0.0, 1.0, 2.0, 3.0, 601.0])
    budget = RestartBudget(max_restarts=3, window_seconds=600, clock=lambda: next(clock))

    assert [budget.allow() for _ in range(3)] == [True, True, True]
    assert budget.allow() is False
    assert budget.allow() is True


def test_supervisor_retries_an_agent_start_failure_without_taking_web_down():
    class Process:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    attempts = {"web": 0, "agent": 0}
    loops = {"count": 0}
    processes = []
    supervisor = None

    def popen(command, env):
        if command[-1] == "backend.app.trading_agent.worker":
            attempts["agent"] += 1
            if attempts["agent"] < 3:
                raise OSError("synthetic worker start failure")
        else:
            attempts["web"] += 1
        process = Process()
        processes.append(process)
        return process

    def sleep(_):
        loops["count"] += 1
        if attempts["agent"] >= 3:
            supervisor.request_stop()
        elif loops["count"] >= 4:
            supervisor.request_stop()

    supervisor = __import__("app.trading_agent.runtime", fromlist=["ProcessSupervisor"]).ProcessSupervisor(
        SupervisorConfig(agent_enabled=True, web_port="8000", poll_seconds=0, restart_delays=(0, 0, 0)),
        popen=popen,
        sleep=sleep,
        log=lambda _: None,
    )

    assert supervisor.run() == 0
    assert attempts == {"web": 1, "agent": 3}
