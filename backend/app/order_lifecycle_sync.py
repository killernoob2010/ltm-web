"""订单全流程来源同步适配器。

默认不开启。开启后仅执行只读 WPS 下载和只读 IMAP 附件读取，解析通过后以完整
批次原子写入 ``order_lifecycle_*`` 表组；任何来源失败都只记录更新异常，不触碰
旧订单融资表。测试版也可以显式调用 ``run_*_sync`` 使用假客户端或受控落地目录。
"""
from __future__ import annotations

from datetime import datetime, time as day_time, timedelta
from email import policy
from email.parser import BytesParser
from email.header import decode_header
import imaplib
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import db
from .order_finance_wps_sync import WpsOrderFinanceClient, WpsOrderFinanceConfig
from .order_lifecycle import apply_source_batch, parse_email_batch, parse_wps_workbook


logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
WPS_SYNC_TIMES = tuple(day_time(hour, 0) for hour in range(9, 19))
EMAIL_SYNC_TIMES = (day_time(9, 0), day_time(10, 0), day_time(11, 0))
WPS_RETRY_DELAY = timedelta(minutes=5)
_scheduler_lock = threading.Lock()
_scheduler_started = False


class LifecycleSyncError(RuntimeError):
    def __init__(self, source_type: str, stage: str):
        self.source_type = source_type
        self.stage = stage
        super().__init__(f"order_lifecycle_{source_type}_sync_failed stage={stage}")


def _shanghai_now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now(SHANGHAI_TZ)
    return current.astimezone(SHANGHAI_TZ) if current.tzinfo else current.replace(tzinfo=SHANGHAI_TZ)


def _slot_key(current: datetime, slot: day_time) -> str:
    return datetime.combine(current.date(), slot, SHANGHAI_TZ).isoformat(timespec="minutes")


def due_wps_slots(now: datetime, attempted_slots: set[str] | None = None) -> list[str]:
    current = _shanghai_now(now)
    if current.weekday() >= 5:
        return []
    attempted = attempted_slots or set()
    return [key for slot in WPS_SYNC_TIMES if (key := _slot_key(current, slot)) not in attempted and datetime.fromisoformat(key) <= current]


def due_email_slots(now: datetime, attempted_slots: set[str] | None = None) -> list[str]:
    current = _shanghai_now(now)
    if current.weekday() != 0:
        return []
    attempted = attempted_slots or set()
    return [key for slot in EMAIL_SYNC_TIMES if (key := _slot_key(current, slot)) not in attempted and datetime.fromisoformat(key) <= current]


def _set_sync_state(source_type: str, *, success: Optional[str] = None, error: Optional[str] = None) -> None:
    success_field = "wps_last_success_at" if source_type == "wps" else "email_last_success_at"
    error_field = "wps_last_error" if source_type == "wps" else "email_last_error"
    with db.connect() as conn:
        db._exec(conn.cursor(), f"UPDATE order_lifecycle_sync_state SET {success_field} = COALESCE(?, {success_field}), {error_field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (success, error))
        conn.commit()


def run_wps_lifecycle_sync(
    slot_key: str,
    now: Optional[datetime] = None,
    client: Optional[WpsOrderFinanceClient] = None,
) -> dict[str, Any]:
    """Download one WPS workbook read-only, validate and atomically apply it."""
    current = _shanghai_now(now)
    temp_path: Optional[Path] = None
    try:
        active_client = client or WpsOrderFinanceClient(WpsOrderFinanceConfig.from_env(), persist_rotated_token=False)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as handle:
            temp_path = Path(handle.name)
        download = active_client.download_workbook(temp_path)
        parsed = parse_wps_workbook(temp_path)
        if not parsed.get("records"):
            raise LifecycleSyncError("wps", "workbook_validation")
        parsed["source_locator"] = f"wps://{download.file_name}"
        parsed["source_version"] = download.source_version or parsed["source_version"]
        parsed["snapshot_date"] = current.date().isoformat()
        result = apply_source_batch(parsed, imported_by="WPS自动同步")
        _set_sync_state("wps", success=current.isoformat(timespec="seconds"), error=None)
        return {"status": "success", **result}
    except Exception as exc:
        _set_sync_state("wps", error=getattr(exc, "stage", type(exc).__name__))
        if isinstance(exc, LifecycleSyncError):
            raise
        raise LifecycleSyncError("wps", getattr(exc, "stage", type(exc).__name__)) from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _decode_mime_filename(value: str) -> str:
    parts = decode_header(value or "")
    return "".join(part.decode(charset or "utf-8", errors="replace") if isinstance(part, bytes) else str(part) for part, charset in parts)


def _mail_batch_directory_from_imap(now: datetime) -> Path:
    host = (os.getenv("ORDER_LIFECYCLE_IMAP_HOST") or "").strip()
    user = (os.getenv("ORDER_LIFECYCLE_IMAP_USER") or "").strip()
    password = os.getenv("ORDER_LIFECYCLE_IMAP_PASSWORD") or ""
    if not host or not user or not password:
        raise LifecycleSyncError("email", "imap_config")
    mailbox = (os.getenv("ORDER_LIFECYCLE_IMAP_MAILBOX") or "INBOX").strip()
    subject_hint = (os.getenv("ORDER_LIFECYCLE_EMAIL_SUBJECT_HINT") or "钢材出口台账表").strip()
    temp_dir = Path(tempfile.mkdtemp(prefix="order-lifecycle-email-"))
    client = None
    try:
        client = imaplib.IMAP4_SSL(host, int(os.getenv("ORDER_LIFECYCLE_IMAP_PORT", "993")))
        client.login(user, password)
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            raise LifecycleSyncError("email", "imap_select")
        status, data = client.search(None, "ALL")
        if status != "OK":
            raise LifecycleSyncError("email", "imap_search")
        message_ids = (data[0] or b"").split()
        for message_id in reversed(message_ids[-200:]):
            status, payload = client.fetch(message_id, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw_message = next((part[1] for part in payload if isinstance(part, tuple)), None)
            if not raw_message:
                continue
            message = BytesParser(policy=policy.default).parsebytes(raw_message)
            subject = str(message.get("subject") or "")
            if subject_hint not in subject:
                continue
            attachment_count = 0
            for part in message.walk():
                if part.get_content_disposition() != "attachment":
                    continue
                filename = _decode_mime_filename(part.get_filename() or "")
                if not filename.lower().endswith((".xls", ".xlsx")):
                    continue
                (temp_dir / Path(filename).name).write_bytes(part.get_payload(decode=True) or b"")
                attachment_count += 1
            if attachment_count:
                return temp_dir
        raise LifecycleSyncError("email", "mail_not_found")
    except LifecycleSyncError:
        raise
    except Exception as exc:
        raise LifecycleSyncError("email", "imap_read") from exc
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def _mail_batch_directory(now: datetime) -> Path:
    landing = (os.getenv("ORDER_LIFECYCLE_EMAIL_LANDING_DIR") or "").strip()
    if landing:
        path = Path(landing)
        if path.exists() and path.is_dir():
            return path
        raise LifecycleSyncError("email", "landing_dir")
    return _mail_batch_directory_from_imap(now)


def run_email_lifecycle_sync(slot_key: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Read one complete six-mill email batch without changing mailbox state."""
    current = _shanghai_now(now)
    directory = _mail_batch_directory(current)
    try:
        parsed = parse_email_batch(directory)
        result = apply_source_batch(parsed, imported_by="邮件台账自动同步")
        _set_sync_state("email", success=current.isoformat(timespec="seconds"), error=None)
        return {"status": "success", **result}
    except Exception as exc:
        _set_sync_state("email", error=getattr(exc, "stage", type(exc).__name__))
        if isinstance(exc, LifecycleSyncError):
            raise
        raise LifecycleSyncError("email", getattr(exc, "stage", type(exc).__name__)) from exc
    finally:
        if directory.name.startswith("order-lifecycle-email-") and directory.exists():
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()


def _scheduler_loop(interval_seconds: int = 30) -> None:
    attempted: dict[str, set[str]] = {"wps": set(), "email": set()}
    failures: dict[tuple[str, str], datetime] = {}
    while True:
        now = _shanghai_now()
        for source_type, slots in (("wps", due_wps_slots(now, attempted["wps"])), ("email", due_email_slots(now, attempted["email"]))):
            for slot_key in slots:
                attempted[source_type].add(slot_key)
                try:
                    if source_type == "wps":
                        run_wps_lifecycle_sync(slot_key, now=now)
                    else:
                        run_email_lifecycle_sync(slot_key, now=now)
                except Exception:
                    failures[(source_type, slot_key)] = now
        for (source_type, slot_key), failed_at in list(failures.items()):
            if now - failed_at < WPS_RETRY_DELAY:
                continue
            failures.pop((source_type, slot_key), None)
            try:
                if source_type == "wps":
                    run_wps_lifecycle_sync(f"{slot_key}:retry", now=now)
                else:
                    run_email_lifecycle_sync(f"{slot_key}:retry", now=now)
            except Exception:
                logger.exception("order_lifecycle_%s_retry_failed", source_type)
        # Keep only recent slots so a long-running process does not grow memory.
        cutoff = (now - timedelta(days=3)).isoformat(timespec="minutes")
        for source_type in attempted:
            attempted[source_type] = {slot for slot in attempted[source_type] if slot >= cutoff}
        time.sleep(interval_seconds)


def start_order_lifecycle_sync_scheduler(interval_seconds: int = 30) -> bool:
    global _scheduler_started
    if (os.getenv("ORDER_LIFECYCLE_AUTO_SYNC_ENABLED") or "").strip().lower() != "true":
        return False
    with _scheduler_lock:
        if _scheduler_started:
            return False
        thread = threading.Thread(target=_scheduler_loop, args=(interval_seconds,), name="order-lifecycle-sync", daemon=True)
        thread.start()
        _scheduler_started = True
    return True
