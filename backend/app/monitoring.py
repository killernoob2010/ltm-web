import os
import threading
import time
from datetime import datetime


_state = {
    "enabled": False,
    "interval_seconds": 30,
    "last_started_at": None,
    "last_finished_at": None,
    "last_success_at": None,
    "last_error": None,
    "is_running": False,
    "duration_ms": None,
}
_started = False
_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv("ENABLE_MONITORING_LOOP", "false").lower() in {"1", "true", "yes", "on"}


def _interval() -> int:
    try:
        return max(5, int(os.getenv("MONITORING_INTERVAL_SECONDS", "30")))
    except ValueError:
        return 30


def _run_once() -> None:
    start = time.perf_counter()
    with _lock:
        if _state["is_running"]:
            return
        _state["is_running"] = True
        _state["last_started_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        # Current project already has alert scan and close-cache schedulers in main.py.
        # This loop is a stable extension point for future summary/monitor refresh work.
        with _lock:
            _state["last_success_at"] = datetime.now().isoformat(timespec="seconds")
            _state["last_error"] = None
    except Exception as exc:
        with _lock:
            _state["last_error"] = str(exc)
    finally:
        with _lock:
            _state["last_finished_at"] = datetime.now().isoformat(timespec="seconds")
            _state["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
            _state["is_running"] = False


def start_monitoring_loop() -> None:
    global _started
    if _started:
        return
    enabled = _enabled()
    interval = _interval()
    with _lock:
        _state["enabled"] = enabled
        _state["interval_seconds"] = interval
    if not enabled:
        return
    _started = True

    def loop() -> None:
        while True:
            _run_once()
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()


def get_monitoring_status() -> dict:
    with _lock:
        status = dict(_state)
    status["enabled"] = _enabled()
    status["interval_seconds"] = _interval()
    return status
