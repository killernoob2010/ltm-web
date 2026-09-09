"""Small process supervisor for the shared Render Web Service runtime.

The Web process remains the service's availability boundary.  The Agent worker
is optional and may be restarted a few times without taking the Web process
down.  This module deliberately has no database, model, or business imports so
the supervisor can still start the Web service when Agent configuration is
incomplete.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
import signal
import subprocess
import sys
import time
from typing import Callable, Deque, Dict, Mapping, Sequence


TRUE_VALUES = {"1", "true", "yes", "on"}


def env_enabled(name: str, *, environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get(name, "false").strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class SupervisorConfig:
    """Runtime limits for one Render service instance."""

    agent_enabled: bool
    web_port: str
    max_agent_restarts: int = 3
    restart_window_seconds: float = 600.0
    poll_seconds: float = 1.0
    shutdown_timeout_seconds: float = 20.0
    restart_delays: tuple[float, ...] = (5.0, 15.0, 30.0)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SupervisorConfig":
        values = os.environ if environ is None else environ

        def number(name: str, default: float, minimum: float = 0.0) -> float:
            try:
                return max(minimum, float(values.get(name, default)))
            except (TypeError, ValueError):
                return default

        try:
            max_restarts = max(0, int(values.get("AGENT_V2_MAX_RESTARTS", 3)))
        except (TypeError, ValueError):
            max_restarts = 3
        return cls(
            agent_enabled=env_enabled("AGENT_V2_ENABLED", environ=values),
            web_port=str(values.get("PORT", "8000")),
            max_agent_restarts=max_restarts,
            restart_window_seconds=number("AGENT_V2_RESTART_WINDOW_SECONDS", 600.0),
            poll_seconds=number("AGENT_V2_SUPERVISOR_POLL_SECONDS", 1.0, 0.05),
            shutdown_timeout_seconds=number("AGENT_V2_SHUTDOWN_TIMEOUT_SECONDS", 20.0, 1.0),
        )


def child_commands(config: SupervisorConfig, *, python: str | None = None) -> Dict[str, list[str]]:
    """Return the only child commands the shared service is allowed to run."""
    executable = python or sys.executable
    commands = {
        "web": [
            executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            config.web_port,
        ],
    }
    if config.agent_enabled:
        commands["agent"] = [executable, "-m", "backend.app.trading_agent.worker"]
    return commands


class RestartBudget:
    """Bound worker restarts to a rolling window."""

    def __init__(self, *, max_restarts: int = 3, window_seconds: float = 600.0,
                 clock: Callable[[], float] = time.monotonic):
        self.max_restarts = max(0, int(max_restarts))
        self.window_seconds = max(0.0, float(window_seconds))
        self.clock = clock
        self._failures: Deque[float] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

    def allow(self) -> bool:
        now = self.clock()
        self._prune(now)
        if len(self._failures) >= self.max_restarts:
            return False
        self._failures.append(now)
        return True

    @property
    def failures(self) -> int:
        self._prune(self.clock())
        return len(self._failures)


class ProcessSupervisor:
    """Keep Web available while giving the optional Agent a bounded lifecycle."""

    def __init__(self, config: SupervisorConfig, *, popen=subprocess.Popen,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print):
        self.config = config
        self._popen = popen
        self._sleep = sleep
        self._clock = clock
        self._log = log
        self._stop_requested = False
        self._children: Dict[str, object] = {}
        self._agent_failed = False
        self._agent_disabled = False
        self._budget = RestartBudget(
            max_restarts=config.max_agent_restarts,
            window_seconds=config.restart_window_seconds,
            clock=clock,
        )

    def request_stop(self, *_signal_args) -> None:
        self._stop_requested = True

    def _spawn(self, name: str, command: Sequence[str]):
        self._log(f"启动 {name} 进程")
        process = self._popen(list(command), env=os.environ.copy())
        self._children[name.lower()] = process
        return process

    def _stop_child(self, process) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=self.config.shutdown_timeout_seconds)
        except (subprocess.TimeoutExpired, OSError):
            try:
                process.kill()
                process.wait(timeout=min(5.0, self.config.shutdown_timeout_seconds))
            except (subprocess.TimeoutExpired, OSError):
                self._log("子进程未能在关闭期限内退出")

    def _install_signal_handlers(self):
        previous = {}
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self.request_stop)
            except (OSError, RuntimeError, ValueError):
                # Unit tests and embedded callers may not run in the main thread.
                continue
        return previous

    @staticmethod
    def _restore_signal_handlers(previous) -> None:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, RuntimeError, ValueError):
                continue

    def run(self) -> int:
        commands = child_commands(self.config)
        previous_handlers = self._install_signal_handlers()
        try:
            web = self._spawn("Web", commands["web"])
            if "agent" in commands:
                try:
                    self._spawn("Agent", commands["agent"])
                except OSError as exc:
                    self._agent_failed = True
                    self._log(f"Agent 启动失败：{type(exc).__name__}")

            while not self._stop_requested:
                web_code = web.poll()
                if web_code is not None:
                    self._log(f"Web 进程已退出（{web_code}），由平台负责恢复服务")
                    return int(web_code or 1)

                agent = self._children.get("agent")
                if agent is not None and agent.poll() is not None:
                    self._children.pop("agent", None)
                    self._agent_failed = True
                if "agent" in commands and self._agent_failed and not self._agent_disabled:
                    if not self._budget.allow():
                        self._log("Agent 在重启窗口内连续失败，保持停用并继续提供 Web")
                        self._agent_disabled = True
                    else:
                        delay_index = min(self._budget.failures - 1, len(self.config.restart_delays) - 1)
                        delay = self.config.restart_delays[max(0, delay_index)] if self.config.restart_delays else 0.0
                        if delay:
                            self._sleep(delay)
                        if not self._stop_requested:
                            try:
                                self._spawn("Agent", commands["agent"])
                                self._agent_failed = False
                            except OSError as exc:
                                self._log(f"Agent 重启失败：{type(exc).__name__}")
                self._sleep(self.config.poll_seconds)
            return 0
        finally:
            for process in list(self._children.values()):
                self._stop_child(process)
            self._children.clear()
            self._restore_signal_handlers(previous_handlers)


def run_from_env() -> int:
    return ProcessSupervisor(SupervisorConfig.from_env()).run()


if __name__ == "__main__":
    raise SystemExit(run_from_env())
