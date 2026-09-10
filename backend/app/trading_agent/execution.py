"""Per-tool cancellation and bounded diagnostic timings; never log business payloads."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
from threading import Event
import time

TASK_SECONDS = 90
TOOL_SECONDS = 25
LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 5
logger = logging.getLogger(__name__)


@dataclass
class ToolExecution:
    deadline: float
    cancelled: Event = field(default_factory=Event)


current: ContextVar[ToolExecution | None] = ContextVar('agent_tool_execution', default=None)


def checkpoint():
    execution = current.get()
    if execution and (execution.cancelled.is_set() or time.monotonic() >= execution.deadline):
        raise TimeoutError('tool_execution_expired')


@contextmanager
def phase(name):
    checkpoint()
    started = time.monotonic()
    outcome = 'complete'
    try:
        yield
        checkpoint()
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        logger.warning('agent_phase phase=%s duration_seconds=%d outcome=%s',
                       name, int(time.monotonic() - started), outcome)
