"""Single-scan and polling orchestration for the local collector."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import hashlib
from pathlib import Path
import time as time_module
from typing import Any, Deque, Dict, List, Optional, Tuple

from .account import confirm_weak_binding
from .models import AccountIdentity, FillRecord, ParseIssue, PositionSnapshot, SourceFile
from .parser import parse_match_records, parse_position_snapshot


@dataclass(frozen=True)
class ScanBatch:
    fills: List[FillRecord]
    issues: List[ParseIssue]
    checkpoint: Dict[str, object]
    position_snapshot: Optional[PositionSnapshot] = None
    priority: str = "history"
    source_kind: str = "match"


class DualChannelScheduler:
    """Small deterministic scheduler; queue draining is always realtime first."""

    def __init__(self, *, realtime_interval: float = 2, position_interval: float = 5, history_interval: float = 10):
        self.realtime_interval = float(realtime_interval)
        self.position_interval = float(position_interval)
        self.history_interval = float(history_interval)
        self._queues: Dict[str, Deque[Any]] = {"realtime": deque(), "history": deque()}
        self._ready: Dict[str, Deque[Any]] = {"realtime": deque(), "history": deque()}
        self._last_tick: Dict[str, Optional[float]] = {"realtime": None, "position": None, "history": None}

    def enqueue_realtime(self, source: Any) -> None:
        self._queues["realtime"].append(source)

    def enqueue_history(self, source: Any) -> None:
        self._queues["history"].append(source)

    def tick(self, now: Optional[float] = None) -> None:
        current = time_module.monotonic() if now is None else float(now)
        for priority in ("realtime", "history"):
            queue = self._queues[priority]
            if not queue:
                continue
            kind = "position" if priority == "realtime" and getattr(queue[0], "kind", "match") == "position" else priority
            interval = self.position_interval if kind == "position" else self.realtime_interval if priority == "realtime" else self.history_interval
            last = self._last_tick[kind]
            if last is None or current - last >= interval:
                self._last_tick[kind] = current
                while queue:
                    self._ready[priority].append(queue.popleft())

    def next_task(self) -> Optional[Tuple[str, Any]]:
        for priority in ("realtime", "history"):
            if self._ready[priority]:
                return priority, self._ready[priority].popleft()
        return None


def scan_source(
    source: SourceFile,
    checkpoint: Optional[Dict[str, object]] = None,
    *,
    account: Optional[AccountIdentity] = None,
) -> ScanBatch:
    path = Path(source.path)
    if not path.is_file() or not source.readable:
        label = "持仓" if source.kind == "position" else "成交"
        issue = ParseIssue("path_unavailable", "WH6 %s缓存路径不可读取，已保留本地队列" % label, str(path))
        return ScanBatch([], [issue], checkpoint or {}, priority="realtime" if source.kind == "position" else "history", source_kind=source.kind)
    try:
        data = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        return ScanBatch([], [ParseIssue("path_unavailable", str(exc), str(path))], checkpoint or {}, priority="realtime" if source.kind == "position" else "history", source_kind=source.kind)
    file_hash = hashlib.sha256(data).hexdigest()
    next_account = account or confirm_weak_binding(source.account_clue or "宏源期货账户待确认", str(path))
    previous_hash = str((checkpoint or {}).get("file_sha256") or "")
    previous_size = int((checkpoint or {}).get("size") or -1)
    if source.kind == "position":
        if previous_hash == file_hash and previous_size == stat.st_size:
            return ScanBatch([], [], checkpoint or {}, priority="realtime", source_kind="position")
        try:
            snapshot, issues = parse_position_snapshot(path, account=next_account, source_file=source)
        except (OSError, ValueError, IndexError, OverflowError) as exc:
            issue = ParseIssue("unknown_format", str(exc), str(path), file_sha256=file_hash, severity="error")
            return ScanBatch([], [issue], checkpoint or {}, priority="realtime", source_kind="position")
        if snapshot is None:
            return ScanBatch([], issues, checkpoint or {}, priority="realtime", source_kind="position")
        checkpoint_value = {
            "file_sha256": file_hash,
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
            "complete": True,
            "snapshot_key": snapshot.source_snapshot_key,
            "row_count": len(snapshot.rows),
        }
        return ScanBatch([], issues, checkpoint_value, position_snapshot=snapshot, priority="realtime", source_kind="position")
    try:
        fills, issues = parse_match_records(path, account=next_account, source_file=source)
    except (OSError, ValueError, IndexError, OverflowError) as exc:
        issue = ParseIssue("unknown_format", str(exc), str(path), file_sha256=file_hash, severity="error")
        return ScanBatch([], [issue], checkpoint or {})
    previous_count = int((checkpoint or {}).get("record_count") or 0)
    if previous_hash == file_hash and previous_size == stat.st_size:
        fills = [fill for fill in fills if fill.source_record_index >= previous_count]
    elif previous_hash and previous_hash != file_hash:
        issues.append(ParseIssue("file_replaced", "文件已轮换或重新生成，已从文件头重新核对事件身份", str(path), file_sha256=file_hash))
    declared_count = int.from_bytes(data[8:12], "little") if len(data) >= 12 else 0
    checkpoint_value: Dict[str, object] = {
        "file_sha256": file_hash,
        "size": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "record_count": declared_count,
    }
    # The checkpoint must represent the scanned file, not just option rows.
    if previous_hash != file_hash or previous_size != stat.st_size:
        checkpoint_value["record_count"] = declared_count
    return ScanBatch(fills, issues, checkpoint_value)


def poll_source(source: SourceFile, *, account: AccountIdentity, outbox, interval_seconds: int = 10, stop_event=None):
    """Poll until stop_event is set; callers provide upload/drain separately."""
    checkpoint = outbox.load_checkpoint(str(source.path))
    while stop_event is None or not stop_event.is_set():
        batch = scan_source(source, checkpoint, account=account)
        outbox.put_many(batch.fills, priority=batch.priority)
        if batch.position_snapshot is not None:
            outbox.put_position(batch.position_snapshot, priority=batch.priority)
        for issue in batch.issues:
            outbox.add_issue(issue)
        checkpoint = batch.checkpoint
        outbox.save_checkpoint(str(source.path), checkpoint, kind=batch.source_kind)
        if stop_event is None:
            break
        stop_event.wait(max(1, int(interval_seconds)))
    return checkpoint
