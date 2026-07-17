"""Idempotent incremental synchronization for iron-ore basis data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
import hashlib
import json
import os
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from . import db
from .iron_ore_basis_calculation import BasisCalculationInput, calculate_basis_row
from .iron_ore_basis_import import DETAIL_COLUMNS, RESULT_COLUMNS
from .iron_ore_basis_rules import BasisRulePack, load_active_rule_pack
from .iron_ore_basis_sources import EbcBasisSource, SinaI0Source, SourcePoint


_SYNC_LOCK = threading.Lock()
_SCHEDULER_STARTED = False
API_START_DATE = date(2026, 7, 13)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
MAX_BATCH_ATTEMPTS = 3


@dataclass(frozen=True)
class BasisSources:
    ebc: Any
    sina: Any


@dataclass(frozen=True)
class SyncSummary:
    status: str
    target_start_date: date
    target_end_date: date
    source_points_seen: int = 0
    source_points_inserted: int = 0
    source_differences: int = 0
    combinations_written: int = 0
    combinations_skipped: int = 0


@dataclass(frozen=True)
class SyncSlot:
    slot_key: str
    trigger_type: str
    target_start_date: date
    target_end_date: date


def auto_sync_enabled() -> bool:
    return os.getenv("IRON_ORE_BASIS_AUTO_SYNC_ENABLED", "").strip().lower() == "true"


def startup_sync_window(latest_result_date: date | None, today: date) -> tuple[date, date]:
    lookback_start = (latest_result_date - timedelta(days=10)) if latest_result_date else API_START_DATE
    return max(API_START_DATE, lookback_start), today


def startup_target_date(current: datetime) -> date:
    shanghai_now = (
        current.replace(tzinfo=SHANGHAI_TZ)
        if current.tzinfo is None
        else current.astimezone(SHANGHAI_TZ)
    )
    if shanghai_now.time() >= datetime_time(21, 30):
        return shanghai_now.date()
    return shanghai_now.date() - timedelta(days=1)


def due_sync_slots(now: datetime) -> list[SyncSlot]:
    current = now.replace(tzinfo=SHANGHAI_TZ) if now.tzinfo is None else now.astimezone(SHANGHAI_TZ)
    today = current.date()
    slots: list[SyncSlot] = []
    morning_start = max(API_START_DATE, today - timedelta(days=10))
    morning_end = today - timedelta(days=1)
    for scheduled_time, label in (
        (datetime_time(9, 30), "0930"),
        (datetime_time(10, 30), "1030"),
    ):
        if current.time() >= scheduled_time and morning_start <= morning_end:
            slots.append(
                SyncSlot(
                    slot_key=f"scheduled:{today.isoformat()}:{label}",
                    trigger_type=f"scheduled_{label}",
                    target_start_date=morning_start,
                    target_end_date=morning_end,
                )
            )
    if current.time() >= datetime_time(21, 30) and today >= API_START_DATE:
        slots.append(
            SyncSlot(
                slot_key=f"scheduled:{today.isoformat()}:2130",
                trigger_type="scheduled_2130",
                target_start_date=today,
                target_end_date=today,
            )
        )
    return slots


def _source_hash(source_name: str, indicator_key: str, business_date: date, value: float) -> str:
    encoded = json.dumps(
        {
            "source_name": source_name,
            "indicator_key": indicator_key,
            "business_date": business_date.isoformat(),
            "value": float(value),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _collect_points(
    sources: BasisSources,
    rule_pack: BasisRulePack,
    start_date: date,
    end_date: date,
) -> list[SourcePoint]:
    points = sources.ebc.fetch_points(list(rule_pack.indicators), start_date, end_date)
    closes = sources.sina.fetch_closes(start_date, end_date)
    points.extend(
        SourcePoint(
            source_name="Sina",
            indicator_key="I0",
            business_date=business_date,
            value=float(value),
            payload_sha256=_source_hash("Sina", "I0", business_date, float(value)),
        )
        for business_date, value in closes.items()
    )
    return points


def _date_range(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _point_map(points: list[SourcePoint]) -> dict[tuple[str, str, date], float]:
    return {
        (point.source_name, point.indicator_key, point.business_date): float(point.value)
        for point in points
        if point.value is not None
    }


def _calculations_from_points(
    point_values: dict[tuple[str, str, date], float],
    rule_pack: BasisRulePack,
    start_date: date,
    end_date: date,
):
    calculations = []
    skipped = 0
    for business_date in _date_range(start_date, end_date):
        futures_close = point_values.get(("Sina", "I0", business_date))
        for mapping in rule_pack.indicators.values():
            wet_spot_price = point_values.get(("EBC", mapping.indicator_code, business_date))
            product_rule = rule_pack.products.get(mapping.product)
            if futures_close is None or wet_spot_price is None or product_rule is None:
                skipped += 1
                continue
            calculations.append(
                calculate_basis_row(
                    BasisCalculationInput(
                        business_date=business_date,
                        mapping=mapping,
                        product_rule=product_rule,
                        wet_spot_price=wet_spot_price,
                        futures_close=futures_close,
                    ),
                    rule_pack,
                )
            )
    return calculations, skipped


def _try_database_lock(cur) -> bool:
    if not db._is_pg():
        return True
    row = db._exec(
        cur,
        "SELECT pg_try_advisory_xact_lock(hashtext(?)) AS locked",
        ("iron_ore_basis_sync",),
    ).fetchone()
    return bool(row and row["locked"])


def _insert_run(cur, *, slot_key: str, trigger_type: str, start_date: date, end_date: date) -> int | None:
    existing = db._exec(
        cur,
        "SELECT id, status, attempt_count FROM iron_ore_basis_sync_runs WHERE slot_key = ?",
        (slot_key,),
    ).fetchone()
    if existing:
        if existing["status"] != "failed" or int(existing["attempt_count"]) >= MAX_BATCH_ATTEMPTS:
            return None
        db._exec(
            cur,
            """UPDATE iron_ore_basis_sync_runs
               SET status = 'running', attempt_count = attempt_count + 1,
                   source_points_seen = 0, source_points_inserted = 0,
                   source_differences = 0, combinations_written = 0,
                   combinations_skipped = 0, error_code = NULL,
                   error_summary = NULL, error_stage = NULL, http_status = NULL,
                   finished_at = NULL, last_attempt_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (existing["id"],),
        )
        return int(existing["id"])
    db._exec(
        cur,
        """INSERT INTO iron_ore_basis_sync_runs
           (slot_key, trigger_type, target_start_date, target_end_date, status,
            attempt_count, last_attempt_at)
           VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)""",
        (slot_key, trigger_type, start_date.isoformat(), end_date.isoformat(), "running"),
    )
    return db.last_insert_id(cur.connection)


def _record_failed_run(
    *,
    slot_key: str,
    trigger_type: str,
    start_date: date,
    end_date: date,
    error: Exception,
    error_stage: str,
) -> None:
    error_code = str(getattr(error, "code", type(error).__name__))[:100]
    stage = str(getattr(error, "stage", None) or error_stage)[:100]
    http_status = getattr(error, "http_status", None)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(
            cur,
            "SELECT id, attempt_count FROM iron_ore_basis_sync_runs WHERE slot_key = ?",
            (slot_key,),
        ).fetchone()
        if existing:
            db._exec(
                cur,
                """UPDATE iron_ore_basis_sync_runs
                   SET status = 'failed', error_code = ?, error_summary = ?,
                       error_stage = ?, http_status = ?,
                       attempt_count = attempt_count + 1,
                       last_attempt_at = CURRENT_TIMESTAMP,
                       finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (error_code, "同步执行失败", stage, http_status, existing["id"]),
            )
            return
        db._exec(
            cur,
            """INSERT INTO iron_ore_basis_sync_runs
               (slot_key, trigger_type, target_start_date, target_end_date, status,
                error_code, error_summary, error_stage, http_status, attempt_count,
                last_attempt_at, finished_at)
               VALUES (?, ?, ?, ?, 'failed', ?, ?, ?, ?, 1,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                slot_key,
                trigger_type,
                start_date.isoformat(),
                end_date.isoformat(),
                error_code,
                "同步执行失败",
                stage,
                http_status,
            ),
        )


def _observe_point(cur, point: SourcePoint, run_id: int) -> tuple[int, int]:
    if point.value is None:
        return 0, 0
    existing = db._exec(
        cur,
        """SELECT canonical_value FROM iron_ore_basis_source_points
           WHERE source_name = ? AND indicator_key = ? AND business_date = ?""",
        (point.source_name, point.indicator_key, point.business_date.isoformat()),
    ).fetchone()
    if not existing:
        db._exec(
            cur,
            """INSERT INTO iron_ore_basis_source_points
               (source_name, indicator_key, business_date, canonical_value,
                canonical_payload_sha256, first_run_id, last_observed_value,
                last_observed_payload_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                point.source_name,
                point.indicator_key,
                point.business_date.isoformat(),
                float(point.value),
                point.payload_sha256,
                run_id,
                float(point.value),
                point.payload_sha256,
            ),
        )
        return 1, 0
    changed = float(existing["canonical_value"]) != float(point.value)
    db._exec(
        cur,
        """UPDATE iron_ore_basis_source_points
           SET last_observed_value = ?, last_observed_payload_sha256 = ?,
               difference_detected = CASE WHEN ? <> 0 THEN 1 ELSE difference_detected END,
               difference_count = difference_count + CASE WHEN ? <> 0 THEN 1 ELSE 0 END,
               last_observed_at = CURRENT_TIMESTAMP
           WHERE source_name = ? AND indicator_key = ? AND business_date = ?""",
        (
            float(point.value),
            point.payload_sha256,
            int(changed),
            int(changed),
            point.source_name,
            point.indicator_key,
            point.business_date.isoformat(),
        ),
    )
    return 0, int(changed)


def _canonical_points(cur, start_date: date, end_date: date) -> dict[tuple[str, str, date], float]:
    rows = db._exec(
        cur,
        """SELECT source_name, indicator_key, business_date, canonical_value
           FROM iron_ore_basis_source_points
           WHERE business_date >= ? AND business_date <= ?""",
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return {
        (row["source_name"], row["indicator_key"], date.fromisoformat(row["business_date"])): float(row["canonical_value"])
        for row in rows
    }


def _insert_calculation(cur, calculation) -> bool:
    result = calculation.result
    db._exec(
        cur,
        f"""INSERT INTO iron_ore_basis_results ({', '.join(RESULT_COLUMNS)})
            VALUES ({', '.join('?' for _ in RESULT_COLUMNS)})
            ON CONFLICT(business_key) DO NOTHING""",
        tuple(result[column] for column in RESULT_COLUMNS),
    )
    if cur.rowcount != 1:
        return False
    result_id = db.last_insert_id(cur.connection)
    detail = {"result_id": result_id, **calculation.detail}
    db._exec(
        cur,
        f"""INSERT INTO iron_ore_basis_details ({', '.join(DETAIL_COLUMNS)})
            VALUES ({', '.join('?' for _ in DETAIL_COLUMNS)})""",
        tuple(detail[column] for column in DETAIL_COLUMNS),
    )
    return True


def sync_basis_range(
    start_date: date,
    end_date: date,
    *,
    trigger_type: str,
    slot_key: str,
    apply: bool = False,
    sources: BasisSources | None = None,
    rule_pack: BasisRulePack | None = None,
) -> SyncSummary:
    if end_date < start_date:
        raise ValueError("同步结束日期不能早于开始日期")
    pack = rule_pack or load_active_rule_pack(start_date)
    active_sources = sources or BasisSources(ebc=EbcBasisSource(), sina=SinaI0Source())

    if not apply:
        points = _collect_points(active_sources, pack, start_date, end_date)
        calculations, skipped = _calculations_from_points(
            _point_map(points), pack, start_date, end_date
        )
        return SyncSummary(
            status="success" if skipped == 0 else "partial",
            target_start_date=start_date,
            target_end_date=end_date,
            source_points_seen=len(points),
            combinations_written=len(calculations),
            combinations_skipped=skipped,
        )

    db.init_db()
    if not _SYNC_LOCK.acquire(blocking=False):
        return SyncSummary("skipped", start_date, end_date)
    error_stage = "run_claim"
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            if not _try_database_lock(cur):
                return SyncSummary("skipped", start_date, end_date)
            run_id = _insert_run(
                cur,
                slot_key=slot_key,
                trigger_type=trigger_type,
                start_date=start_date,
                end_date=end_date,
            )
            if run_id is None:
                return SyncSummary("skipped", start_date, end_date)

            error_stage = "source_collect"
            points = _collect_points(active_sources, pack, start_date, end_date)
            inserted_points = 0
            differences = 0
            error_stage = "source_observe"
            for point in points:
                inserted, changed = _observe_point(cur, point, run_id)
                inserted_points += inserted
                differences += changed

            error_stage = "calculate"
            calculations, skipped = _calculations_from_points(
                _canonical_points(cur, start_date, end_date),
                pack,
                start_date,
                end_date,
            )
            error_stage = "database_write"
            written = sum(1 for calculation in calculations if _insert_calculation(cur, calculation))
            status = "success" if skipped == 0 else "partial"
            db._exec(
                cur,
                """UPDATE iron_ore_basis_sync_runs
                   SET status = ?, source_points_seen = ?, source_points_inserted = ?,
                       source_differences = ?, combinations_written = ?,
                       combinations_skipped = ?, finished_at = CURRENT_TIMESTAMP,
                       error_code = NULL, error_summary = NULL, error_stage = NULL,
                       http_status = NULL, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    status,
                    len(points),
                    inserted_points,
                    differences,
                    written,
                    skipped,
                    run_id,
                ),
            )
            return SyncSummary(
                status=status,
                target_start_date=start_date,
                target_end_date=end_date,
                source_points_seen=len(points),
                source_points_inserted=inserted_points,
                source_differences=differences,
                combinations_written=written,
                combinations_skipped=skipped,
            )
    except Exception as exc:
        _record_failed_run(
            slot_key=slot_key,
            trigger_type=trigger_type,
            start_date=start_date,
            end_date=end_date,
            error=exc,
            error_stage=error_stage,
        )
        raise
    finally:
        _SYNC_LOCK.release()


def _latest_result_date() -> date | None:
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT MAX(business_date) AS latest_date FROM iron_ore_basis_results",
        ).fetchone()
    return date.fromisoformat(row["latest_date"]) if row and row["latest_date"] else None


def run_startup_basis_sync(now: datetime | None = None) -> SyncSummary:
    current = now or datetime.now(SHANGHAI_TZ)
    current = current.replace(tzinfo=SHANGHAI_TZ) if current.tzinfo is None else current.astimezone(SHANGHAI_TZ)
    start_date, end_date = startup_sync_window(_latest_result_date(), startup_target_date(current))
    return sync_basis_range(
        start_date,
        end_date,
        trigger_type="startup",
        slot_key=f"startup:{current.date().isoformat()}",
        apply=True,
    )


def run_due_basis_syncs(now: datetime | None = None) -> list[SyncSummary]:
    current = now or datetime.now(SHANGHAI_TZ)
    summaries = []
    for slot in due_sync_slots(current):
        try:
            summaries.append(
                sync_basis_range(
                    slot.target_start_date,
                    slot.target_end_date,
                    trigger_type=slot.trigger_type,
                    slot_key=slot.slot_key,
                    apply=True,
                )
            )
        except Exception:
            continue
    return summaries


def _basis_sync_scheduler_loop(interval_seconds: int) -> None:
    try:
        run_startup_basis_sync()
    except Exception:
        pass
    while True:
        run_due_basis_syncs()
        time.sleep(interval_seconds)


def start_iron_ore_basis_source_scheduler(interval_seconds: int = 300) -> bool:
    global _SCHEDULER_STARTED
    if not auto_sync_enabled() or _SCHEDULER_STARTED:
        return False
    _SCHEDULER_STARTED = True
    thread = threading.Thread(
        target=_basis_sync_scheduler_loop,
        args=(interval_seconds,),
        daemon=True,
        name="iron-ore-basis-sync",
    )
    thread.start()
    return True
