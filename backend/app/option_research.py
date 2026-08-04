from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid
from typing import Any, Iterable, Optional

from fastapi import APIRouter

from . import db


router = APIRouter()

OPTION_RESEARCH_TABLES = (
    "option_research_contracts",
    "option_research_bars",
    "option_research_runs",
    "option_research_gaps",
)
PROBE_PREFIX = "OPTION_RESEARCH_PROBE="
PROBE_VERSION = "v2"
PROBE_TIMEOUT_SECONDS = 180
_DCE_IRON_ORE_OPTION_RE = re.compile(
    r"^DCE\.(?P<underlying>i\d{4})-(?P<option_class>[CP])-(?P<strike>\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_KNOWN_ERROR_MESSAGES = {
    "credentials_missing": "Render 未配置天勤行情认证，历史期权探针未运行。",
    "auth_failed": "天勤认证失败，需核对 Staging 环境中的认证配置。",
    "no_iron_ore_futures": "天勤未返回铁矿石具体期货合约。",
    "no_option_contracts": "已找到铁矿石期货，但未找到对应期权合约。",
    "daily_history_unavailable": "已找到期权合约，但样本历史日线不可用。",
    "five_minute_history_unavailable": "样本日线可读，但普通接口未返回有效的 5 分钟历史行情。",
    "downloader_unavailable": "期权合约和样本日线可读，但当前账号没有专业历史下载权限。",
    "probe_timeout": "天勤历史期权能力检查超时。",
    "probe_process_failed": "天勤历史期权能力检查进程异常结束。",
    "tqsdk_probe_failed": "天勤历史期权能力检查失败。",
    "ready": "真实历史期权采集的三项前置能力均已通过。",
}

_probe_thread: Optional[threading.Thread] = None
_probe_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id_column() -> str:
    return "SERIAL PRIMARY KEY" if db._is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def ensure_schema() -> None:
    """Create an isolated research schema without changing trading facts."""
    id_column = _id_column()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS option_research_contracts (
                id {id_column},
                symbol TEXT NOT NULL UNIQUE,
                exchange_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                underlying_symbol TEXT NOT NULL,
                option_class TEXT NOT NULL,
                strike_price DOUBLE PRECISION NOT NULL,
                expire_datetime TEXT,
                expired INTEGER NOT NULL DEFAULT 0,
                volume_multiple DOUBLE PRECISION,
                price_tick DOUBLE PRECISION,
                source TEXT NOT NULL DEFAULT 'tqsdk',
                discovered_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{{}}'
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS option_research_bars (
                id {id_column},
                symbol TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                datetime_nano BIGINT NOT NULL,
                datetime_text TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                open_price DOUBLE PRECISION,
                high_price DOUBLE PRECISION,
                low_price DOUBLE PRECISION,
                close_price DOUBLE PRECISION,
                settlement_price DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                open_interest DOUBLE PRECISION,
                bid_price1 DOUBLE PRECISION,
                ask_price1 DOUBLE PRECISION,
                source TEXT NOT NULL DEFAULT 'tqsdk',
                run_key TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, duration_seconds, datetime_nano)
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS option_research_runs (
                id {id_column},
                run_key TEXT NOT NULL UNIQUE,
                run_type TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'tqsdk',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                requested_start TEXT,
                requested_end TEXT,
                duration_seconds INTEGER,
                futures_count INTEGER NOT NULL DEFAULT 0,
                options_count INTEGER NOT NULL DEFAULT 0,
                bars_count INTEGER NOT NULL DEFAULT 0,
                gaps_count INTEGER NOT NULL DEFAULT 0,
                checks_json TEXT NOT NULL DEFAULT '{{}}',
                error_code TEXT,
                message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS option_research_gaps (
                id {id_column},
                run_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                gap_start TEXT NOT NULL,
                gap_end TEXT NOT NULL,
                gap_type TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_key, symbol, duration_seconds, gap_start, gap_end, gap_type)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_research_contracts_underlying "
            "ON option_research_contracts(underlying_symbol, option_class, strike_price)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_research_bars_lookup "
            "ON option_research_bars(symbol, duration_seconds, datetime_nano)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_research_runs_status "
            "ON option_research_runs(run_type, status, started_at)"
        )
        if db._is_pg():
            db._secure_postgres_tables(cur, OPTION_RESEARCH_TABLES)


def parse_dce_iron_ore_option_symbol(symbol: str) -> Optional[dict[str, Any]]:
    match = _DCE_IRON_ORE_OPTION_RE.match(str(symbol or "").strip())
    if not match:
        return None
    return {
        "symbol": symbol,
        "exchange_id": "DCE",
        "product_id": "i",
        "underlying_symbol": f"DCE.{match.group('underlying').lower()}",
        "option_class": "CALL" if match.group("option_class").upper() == "C" else "PUT",
        "strike_price": float(match.group("strike")),
    }


def bar_quality_issues(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return bounded, deterministic OHLC/data-order issues for imported bars."""
    issues: list[dict[str, Any]] = []
    previous_by_symbol: dict[tuple[str, int], int] = {}
    for index, row in enumerate(rows):
        symbol = str(row.get("symbol") or "")
        duration = int(row.get("duration_seconds") or 0)
        timestamp = int(row.get("datetime_nano") or 0)
        key = (symbol, duration)
        previous = previous_by_symbol.get(key)
        if timestamp <= 0:
            issues.append({"row": index, "code": "invalid_timestamp"})
        elif previous is not None and timestamp <= previous:
            issues.append({"row": index, "code": "non_increasing_timestamp"})
        previous_by_symbol[key] = timestamp

        prices: dict[str, float] = {}
        for name in ("open_price", "high_price", "low_price", "close_price"):
            try:
                value = float(row.get(name))
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value) or value < 0:
                issues.append({"row": index, "code": f"invalid_{name}"})
            else:
                prices[name] = value
        if len(prices) == 4:
            if prices["high_price"] < max(
                prices["open_price"], prices["low_price"], prices["close_price"]
            ):
                issues.append({"row": index, "code": "invalid_high"})
            if prices["low_price"] > min(
                prices["open_price"], prices["high_price"], prices["close_price"]
            ):
                issues.append({"row": index, "code": "invalid_low"})
        for name in ("volume", "open_interest"):
            value = row.get(name)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = math.nan
            if not math.isfinite(number) or number < 0:
                issues.append({"row": index, "code": f"invalid_{name}"})
        if len(issues) >= 100:
            break
    return issues


def _create_probe_run() -> str:
    run_key = f"readiness-{uuid.uuid4().hex}"
    now = _utc_now()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO option_research_runs
            (run_key, run_type, status, source, started_at, checks_json, message)
            VALUES (?, 'readiness_probe', 'running', 'tqsdk', ?, '{}', ?)
            """,
            (run_key, now, "正在检查历史铁矿石期权数据能力。"),
        )
    return run_key


def _finish_probe_run(run_key: str, result: dict[str, Any]) -> None:
    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    status = str(result.get("status") or "blocked")
    error_code = str(result.get("error_code") or "") or None
    message = _KNOWN_ERROR_MESSAGES.get(
        error_code or ("ready" if status == "ready" else "tqsdk_probe_failed"),
        _KNOWN_ERROR_MESSAGES["tqsdk_probe_failed"],
    )
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            UPDATE option_research_runs
               SET status = ?, finished_at = ?, futures_count = ?, options_count = ?,
                   bars_count = ?, checks_json = ?, error_code = ?, message = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE run_key = ?
            """,
            (
                status,
                _utc_now(),
                int(result.get("futures_count") or 0),
                int(result.get("options_count") or 0),
                int(result.get("bars_count") or 0),
                json.dumps(checks, ensure_ascii=False, sort_keys=True),
                error_code,
                message,
                run_key,
            ),
        )


def _safe_probe_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "blocked", "error_code": "probe_process_failed"}
    status = str(payload.get("status") or "blocked")
    if status not in {"ready", "partial", "blocked"}:
        status = "blocked"
    error_code = str(payload.get("error_code") or "")
    if error_code and error_code not in _KNOWN_ERROR_MESSAGES:
        error_code = "tqsdk_probe_failed"
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    safe_checks = {
        "probe_version": str(checks.get("probe_version") or ""),
        "credentials_configured": bool(checks.get("credentials_configured")),
        "iron_ore_futures_discovered": bool(checks.get("iron_ore_futures_discovered")),
        "option_contracts_discovered": bool(checks.get("option_contracts_discovered")),
        "daily_history_available": bool(checks.get("daily_history_available")),
        "five_minute_history_available": bool(
            checks.get("five_minute_history_available")
        ),
        "five_minute_full_lifecycle": bool(
            checks.get("five_minute_full_lifecycle")
        ),
        "sample_five_minute_bars": max(
            0, int(checks.get("sample_five_minute_bars") or 0)
        ),
        "sample_daily_first_date": str(checks.get("sample_daily_first_date") or ""),
        "sample_daily_last_date": str(checks.get("sample_daily_last_date") or ""),
        "sample_five_minute_first_date": str(
            checks.get("sample_five_minute_first_date") or ""
        ),
        "sample_five_minute_last_date": str(
            checks.get("sample_five_minute_last_date") or ""
        ),
        "professional_downloader_available": bool(
            checks.get("professional_downloader_available")
        ),
    }
    return {
        "status": status,
        "error_code": error_code or None,
        "futures_count": max(0, int(payload.get("futures_count") or 0)),
        "options_count": max(0, int(payload.get("options_count") or 0)),
        "bars_count": max(0, int(payload.get("bars_count") or 0)),
        "checks": safe_checks,
    }


def _run_probe_subprocess() -> dict[str, Any]:
    root_dir = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "backend.app.option_research_probe"],
            cwd=root_dir,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "blocked", "error_code": "probe_timeout"}
    if completed.returncode != 0:
        return {"status": "blocked", "error_code": "probe_process_failed"}
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(PROBE_PREFIX):
            try:
                return _safe_probe_result(json.loads(line[len(PROBE_PREFIX):]))
            except (TypeError, ValueError, json.JSONDecodeError):
                break
    return {"status": "blocked", "error_code": "probe_process_failed"}


def _probe_worker() -> None:
    run_key = _create_probe_run()
    result = _run_probe_subprocess()
    _finish_probe_run(run_key, result)


def is_staging_environment() -> bool:
    explicit = os.getenv("OPTION_RESEARCH_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    service_name = os.getenv("RENDER_SERVICE_NAME", "").strip().lower()
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip().lower()
    return service_name.endswith("-staging") or "ltm-web-staging" in hostname


def _latest_probe() -> Optional[dict[str, Any]]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """
            SELECT run_key, status, started_at, finished_at, futures_count,
                   options_count, bars_count, checks_json, error_code, message
              FROM option_research_runs
             WHERE run_type = 'readiness_probe'
             ORDER BY id DESC
             LIMIT 1
            """,
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["checks"] = json.loads(result.pop("checks_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        result["checks"] = {}
        result.pop("checks_json", None)
    return result


def _probe_is_fresh(latest: Optional[dict[str, Any]]) -> bool:
    if not latest or latest.get("status") == "running":
        return bool(latest)
    if (latest.get("checks") or {}).get("probe_version") != PROBE_VERSION:
        return False
    finished_at = str(latest.get("finished_at") or "")
    try:
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return False
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    refresh_hours = max(1, int(os.getenv("OPTION_RESEARCH_PROBE_REFRESH_HOURS", "24")))
    return datetime.now(timezone.utc) - finished < timedelta(hours=refresh_hours)


def start_auto_probe() -> bool:
    """Start one bounded readiness probe on Staging; never runs in Production."""
    global _probe_thread
    if not is_staging_environment():
        return False
    with _probe_lock:
        if _probe_thread is not None and _probe_thread.is_alive():
            return False
        if _probe_is_fresh(_latest_probe()):
            return False
        _probe_thread = threading.Thread(
            target=_probe_worker,
            name="option-research-readiness",
            daemon=True,
        )
        _probe_thread.start()
        return True


def readiness_snapshot() -> dict[str, Any]:
    if not is_staging_environment():
        return {
            "enabled": False,
            "status": "disabled",
            "message": "历史期权研究探针只在 Staging 环境启用。",
        }
    try:
        latest = _latest_probe()
    except Exception:
        return {
            "enabled": True,
            "status": "pending",
            "message": "历史期权研究表仍在初始化。",
        }
    if latest is None:
        return {
            "enabled": True,
            "status": "pending",
            "message": "等待首次历史期权能力检查。",
        }
    return {
        "enabled": True,
        "status": latest.get("status"),
        "checked_at": latest.get("finished_at") or latest.get("started_at"),
        "futures_count": int(latest.get("futures_count") or 0),
        "options_count": int(latest.get("options_count") or 0),
        "sample_daily_bars": int(latest.get("bars_count") or 0),
        "checks": latest.get("checks") or {},
        "error_code": latest.get("error_code"),
        "message": latest.get("message"),
    }


@router.get("/option-research/readiness")
def option_research_readiness() -> dict[str, Any]:
    return readiness_snapshot()
