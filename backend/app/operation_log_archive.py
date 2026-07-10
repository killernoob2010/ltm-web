from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
import gzip
import hashlib
import json
import os
from typing import Iterable, Optional
from urllib.parse import quote, urlparse

import requests

from . import db


ARCHIVE_BUCKET = "operation-log-archives"
STANDARD_UPLOAD_LIMIT = 6 * 1024 * 1024
TUS_CHUNK_SIZE = 6 * 1024 * 1024


class ArchiveError(RuntimeError):
    pass


class ArchiveConfigError(ArchiveError):
    pass


class ArchiveStorageError(ArchiveError):
    pass


@dataclass(frozen=True)
class ArchivePeriod:
    period_start: date
    period_end: date
    row_count: int


@dataclass(frozen=True)
class ArchivePayload:
    period: ArchivePeriod
    content: bytes
    sha256: str
    row_count: int
    first_created_at: str
    last_created_at: str
    user_ids: tuple[int, ...]


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def select_archive_periods(
    conn,
    *,
    today: date,
    retention_months: int = 12,
    max_online_rows: int = 200_000,
) -> list[ArchivePeriod]:
    current_month = _month_start(today)
    cutoff_month = _add_months(current_month, -retention_months)
    cur = conn.cursor()
    total = int(db._exec(cur, "SELECT COUNT(*) AS c FROM operation_logs").fetchone()["c"] or 0)
    month_rows = db._exec(
        cur,
        """
        SELECT SUBSTR(created_at, 1, 7) AS month_key, COUNT(*) AS c
        FROM operation_logs
        WHERE created_at < ?
        GROUP BY SUBSTR(created_at, 1, 7)
        ORDER BY month_key ASC
        """,
        (f"{current_month.isoformat()} 00:00:00",),
    ).fetchall()
    remaining = total
    selected: list[ArchivePeriod] = []
    for row in month_rows:
        try:
            start = date.fromisoformat(f"{row['month_key']}-01")
        except (TypeError, ValueError):
            continue
        end = _add_months(start, 1)
        count = int(row["c"] or 0)
        if end <= cutoff_month or remaining > max_online_rows:
            selected.append(ArchivePeriod(start, end, count))
            remaining -= count
    return selected


def build_archive_payload(conn, period: ArchivePeriod) -> ArchivePayload:
    rows = db._exec(
        conn.cursor(),
        """
        SELECT ol.id, ol.user_id, u.name AS user_name, ol.module_code, ol.entity_type,
               ol.entity_id, ol.operation_type, ol.description, ol.before_data,
               ol.after_data, ol.created_at
        FROM operation_logs ol
        LEFT JOIN users u ON u.id = ol.user_id
        WHERE ol.created_at >= ? AND ol.created_at < ?
        ORDER BY ol.created_at ASC, ol.id ASC
        """,
        (
            f"{period.period_start.isoformat()} 00:00:00",
            f"{period.period_end.isoformat()} 00:00:00",
        ),
    ).fetchall()
    if not rows:
        raise ArchiveError("归档月份没有可归档日志")
    raw_lines = []
    user_ids = set()
    for row in rows:
        item = dict(row)
        if item.get("user_id") is not None:
            user_ids.add(int(item["user_id"]))
        raw_lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    raw = ("\n".join(raw_lines) + "\n").encode("utf-8")
    content = gzip.compress(raw, mtime=0)
    return ArchivePayload(
        period=period,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        row_count=len(rows),
        first_created_at=str(rows[0]["created_at"]),
        last_created_at=str(rows[-1]["created_at"]),
        user_ids=tuple(sorted(user_ids)),
    )


class SupabaseArchiveStorage:
    def __init__(
        self,
        project_url: str,
        service_role_key: str,
        *,
        bucket: str = ARCHIVE_BUCKET,
        session=requests,
    ):
        self.project_url = project_url.rstrip("/")
        self.service_role_key = service_role_key
        self.bucket = bucket
        self.session = session

    @classmethod
    def from_env(cls, *, session=requests) -> "SupabaseArchiveStorage":
        project_url = os.getenv("SUPABASE_URL", "").strip()
        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not project_url or not service_role_key:
            raise ArchiveConfigError("归档存储未配置")
        return cls(project_url, service_role_key, session=session)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
        }

    def _object_url(self, path: str, *, authenticated: bool = False) -> str:
        mode = "authenticated" if authenticated else self.bucket
        escaped = quote(path.lstrip("/"), safe="/")
        if authenticated:
            return f"{self.project_url}/storage/v1/object/authenticated/{self.bucket}/{escaped}"
        return f"{self.project_url}/storage/v1/object/{mode}/{escaped}"

    def validate_private_bucket(self) -> None:
        response = self.session.get(
            f"{self.project_url}/storage/v1/bucket/{quote(self.bucket, safe='')}",
            headers=self._headers(),
            timeout=30,
        )
        if response.status_code != 200:
            raise ArchiveStorageError(f"私有归档 bucket 不存在或不可访问（HTTP {response.status_code}）")
        try:
            is_public = bool(response.json().get("public"))
        except (ValueError, AttributeError) as exc:
            raise ArchiveStorageError("归档 bucket 配置响应无效") from exc
        if is_public:
            raise ArchiveStorageError("归档 bucket 必须设为私有")

    def upload_immutable(self, path: str, content: bytes) -> None:
        if len(content) <= STANDARD_UPLOAD_LIMIT:
            response = self.session.post(
                self._object_url(path),
                headers={**self._headers(), "Content-Type": "application/gzip", "x-upsert": "false"},
                data=content,
                timeout=60,
            )
            if response.status_code not in {200, 201}:
                raise ArchiveStorageError(f"归档上传失败（HTTP {response.status_code}）")
            return
        self._upload_tus(path, content)

    def _upload_tus(self, path: str, content: bytes) -> None:
        project_id = urlparse(self.project_url).hostname.split(".")[0]
        endpoint = f"https://{project_id}.storage.supabase.co/storage/v1/upload/resumable"
        metadata = {
            "bucketName": self.bucket,
            "objectName": path.lstrip("/"),
            "contentType": "application/gzip",
            "cacheControl": "0",
        }
        encoded_metadata = ",".join(
            f"{key} {base64.b64encode(value.encode('utf-8')).decode('ascii')}"
            for key, value in metadata.items()
        )
        response = self.session.post(
            endpoint,
            headers={
                **self._headers(),
                "Tus-Resumable": "1.0.0",
                "Upload-Length": str(len(content)),
                "Upload-Metadata": encoded_metadata,
                "x-upsert": "false",
            },
            timeout=60,
        )
        if response.status_code not in {201, 204} or not response.headers.get("Location"):
            raise ArchiveStorageError(f"归档分块上传初始化失败（HTTP {response.status_code}）")
        upload_url = response.headers["Location"]
        offset = 0
        while offset < len(content):
            chunk = content[offset : offset + TUS_CHUNK_SIZE]
            patch_response = self.session.patch(
                upload_url,
                headers={
                    **self._headers(),
                    "Tus-Resumable": "1.0.0",
                    "Upload-Offset": str(offset),
                    "Content-Type": "application/offset+octet-stream",
                },
                data=chunk,
                timeout=60,
            )
            if patch_response.status_code != 204:
                raise ArchiveStorageError(f"归档分块上传失败（HTTP {patch_response.status_code}）")
            offset = int(patch_response.headers.get("Upload-Offset", offset + len(chunk)))

    def download(self, path: str) -> bytes:
        response = self.session.get(
            self._object_url(path, authenticated=True),
            headers=self._headers(),
            timeout=60,
        )
        if response.status_code != 200:
            raise ArchiveStorageError(f"归档下载失败（HTTP {response.status_code}）")
        return response.content

    def verify(self, path: str, expected_sha256: str, expected_bytes: int) -> bool:
        content = self.download(path)
        return len(content) == expected_bytes and hashlib.sha256(content).hexdigest() == expected_sha256

    def iter_download(self, path: str, chunk_size: int = 64 * 1024) -> Iterable[bytes]:
        response = self.session.get(
            self._object_url(path, authenticated=True),
            headers=self._headers(),
            timeout=60,
            stream=True,
        )
        if response.status_code != 200:
            raise ArchiveStorageError(f"归档下载失败（HTTP {response.status_code}）")
        yield from response.iter_content(chunk_size=chunk_size)


def _object_path(environment: str, period: ArchivePeriod) -> str:
    return (
        f"{environment}/{period.period_start.year:04d}/{period.period_start.month:02d}/"
        f"operation-logs-{period.period_start.year:04d}-{period.period_start.month:02d}.ndjson.gz"
    )


@contextmanager
def archive_run_lock():
    if not db._is_pg():
        yield
        return
    with db.connect() as conn:
        acquired = db._exec(
            conn.cursor(),
            "SELECT pg_try_advisory_lock(784512903) AS acquired",
        ).fetchone()["acquired"]
        if not acquired:
            raise ArchiveError("已有另一个操作日志归档任务正在执行")
        try:
            yield
        finally:
            db._exec(conn.cursor(), "SELECT pg_advisory_unlock(784512903)")


def archive_due_logs(
    storage,
    *,
    apply: bool,
    today: Optional[date] = None,
    environment: str = "staging",
    retention_months: int = 12,
    max_online_rows: int = 200_000,
) -> dict:
    with archive_run_lock():
        return _archive_due_logs_unlocked(
            storage,
            apply=apply,
            today=today,
            environment=environment,
            retention_months=retention_months,
            max_online_rows=max_online_rows,
        )


def _archive_due_logs_unlocked(
    storage,
    *,
    apply: bool,
    today: Optional[date],
    environment: str,
    retention_months: int,
    max_online_rows: int,
) -> dict:
    today = today or date.today()
    with db.connect() as conn:
        periods = select_archive_periods(
            conn,
            today=today,
            retention_months=retention_months,
            max_online_rows=max_online_rows,
        )
    result = {
        "apply": apply,
        "candidate_months": len(periods),
        "candidate_rows": sum(period.row_count for period in periods),
        "archived_rows": 0,
        "archives": [],
    }
    if not apply:
        return result

    if periods:
        storage.validate_private_bucket()

    for period in periods:
        with db.connect() as conn:
            existing = db._exec(
                conn.cursor(),
                "SELECT * FROM operation_log_archives WHERE period_start = ? AND period_end = ?",
                (period.period_start.isoformat(), period.period_end.isoformat()),
            ).fetchone()
            if existing:
                result["archives"].append({"id": existing["id"], "status": "already_archived"})
                continue
            payload = build_archive_payload(conn, period)
        path = _object_path(environment, period)
        storage.upload_immutable(path, payload.content)
        if not storage.verify(path, payload.sha256, len(payload.content)):
            raise ArchiveStorageError("归档上传校验失败，在线日志未删除")

        with db.connect() as conn:
            cur = conn.cursor()
            db._exec(
                cur,
                """
                INSERT INTO operation_log_archives
                    (period_start, period_end, object_path, row_count, first_created_at,
                     last_created_at, sha256, compressed_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period.period_start.isoformat(),
                    period.period_end.isoformat(),
                    path,
                    payload.row_count,
                    payload.first_created_at,
                    payload.last_created_at,
                    payload.sha256,
                    len(payload.content),
                ),
            )
            archive_id = db.last_insert_id(conn)
            for user_id in payload.user_ids:
                db._exec(
                    cur,
                    "INSERT INTO operation_log_archive_users (archive_id, user_id) VALUES (?, ?)",
                    (archive_id, user_id),
                )
            deleted = db._exec(
                cur,
                "DELETE FROM operation_logs WHERE created_at >= ? AND created_at < ?",
                (
                    f"{period.period_start.isoformat()} 00:00:00",
                    f"{period.period_end.isoformat()} 00:00:00",
                ),
            ).rowcount
            if deleted != payload.row_count:
                raise ArchiveError("归档删除行数与已校验文件不一致，数据库事务已回滚")
        result["archived_rows"] += payload.row_count
        result["archives"].append({"id": archive_id, "object_path": path, "row_count": payload.row_count})

    if result["archived_rows"]:
        db.log_operation(
            None,
            "operation_logs",
            "归档日志",
            f"归档 {len(result['archives'])} 个月，共 {result['archived_rows']} 条",
            "operation_log_archives",
            None,
        )
    return result


def restore_archive(archive_id: int, storage, *, apply: bool) -> dict:
    with db.connect() as conn:
        metadata = db._exec(
            conn.cursor(),
            "SELECT * FROM operation_log_archives WHERE id = ?",
            (archive_id,),
        ).fetchone()
    if not metadata:
        raise ArchiveError("归档记录不存在")
    if not apply:
        return {"archive_id": archive_id, "candidate_rows": int(metadata["row_count"]), "restored_rows": 0}

    content = storage.download(metadata["object_path"])
    if len(content) != int(metadata["compressed_bytes"]):
        raise ArchiveStorageError("归档文件大小校验失败")
    if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
        raise ArchiveStorageError("归档文件校验和不一致")
    try:
        rows = [json.loads(line) for line in gzip.decompress(content).decode("utf-8").splitlines()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("归档文件格式无效") from exc
    if len(rows) != int(metadata["row_count"]):
        raise ArchiveError("归档文件行数校验失败")
    ids = [int(row["id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ArchiveError("归档文件包含重复日志 ID")

    with db.connect() as conn:
        cur = conn.cursor()
        if ids:
            existing = db._exec(
                cur,
                f"SELECT id FROM operation_logs WHERE id IN ({','.join('?' for _ in ids)}) LIMIT 1",
                ids,
            ).fetchone()
            if existing:
                raise ArchiveError("恢复失败：日志 ID 已存在，数据库未写入")
        for row in rows:
            db._exec(
                cur,
                """
                INSERT INTO operation_logs
                    (id, user_id, module_code, entity_type, entity_id, operation_type,
                     description, before_data, after_data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row.get("user_id"),
                    row.get("module_code"),
                    row.get("entity_type"),
                    row.get("entity_id"),
                    row["operation_type"],
                    row["description"],
                    row.get("before_data"),
                    row.get("after_data"),
                    row["created_at"],
                ),
            )
        db._exec(
            cur,
            "UPDATE operation_log_archives SET restored_at = CURRENT_TIMESTAMP WHERE id = ?",
            (archive_id,),
        )
    db.log_operation(
        None,
        "operation_logs",
        "恢复归档日志",
        f"恢复归档 {archive_id}，共 {len(rows)} 条",
        "operation_log_archives",
        archive_id,
    )
    return {"archive_id": archive_id, "restored_rows": len(rows)}
