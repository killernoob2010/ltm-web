"""Single-scan and polling orchestration for the local collector."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time as time_module
from typing import Dict, List, Optional

from .account import confirm_weak_binding
from .models import AccountIdentity, FillRecord, ParseIssue, SourceFile
from .parser import parse_match_records


@dataclass(frozen=True)
class ScanBatch:
    fills: List[FillRecord]
    issues: List[ParseIssue]
    checkpoint: Dict[str, object]


def scan_source(
    source: SourceFile,
    checkpoint: Optional[Dict[str, object]] = None,
    *,
    account: Optional[AccountIdentity] = None,
) -> ScanBatch:
    path = Path(source.path)
    if not path.is_file() or not source.readable:
        issue = ParseIssue("path_unavailable", "WH6 成交缓存路径不可读取，已保留本地队列", str(path))
        return ScanBatch([], [issue], checkpoint or {})
    try:
        data = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        return ScanBatch([], [ParseIssue("path_unavailable", str(exc), str(path))], checkpoint or {})
    file_hash = hashlib.sha256(data).hexdigest()
    next_account = account or confirm_weak_binding(source.account_clue or "宏源期货账户待确认", str(path))
    try:
        fills, issues = parse_match_records(path, account=next_account, source_file=source)
    except (OSError, ValueError, IndexError) as exc:
        issue = ParseIssue("unknown_format", str(exc), str(path), file_sha256=file_hash, severity="error")
        return ScanBatch([], [issue], checkpoint or {})
    previous_hash = str((checkpoint or {}).get("file_sha256") or "")
    previous_size = int((checkpoint or {}).get("size") or -1)
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
        outbox.put_many(batch.fills)
        for issue in batch.issues:
            outbox.add_issue(issue)
        checkpoint = batch.checkpoint
        outbox.save_checkpoint(str(source.path), checkpoint)
        if stop_event is None:
            break
        stop_event.wait(max(1, int(interval_seconds)))
    return checkpoint
