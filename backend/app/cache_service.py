from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Optional

from . import db


DESKTOP_SOURCE_ZIP = Path("/Users/wangjingze/Desktop/v6.04_source_code.zip")
DESKTOP_CACHE_SUFFIX = "modules\\info_alert\\data_cache.db"


def month_matches_clause() -> str:
    return "(month = ? OR (month IS NULL AND ? IS NULL))"


def get_cached_data(info_type: str, year: int, month: Optional[str], calc_date: str) -> Optional[dict]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, 
            f"""
            SELECT t_1_value, t_2_value, mean_value, min_value, max_value, std_value
            FROM calculated_data
            WHERE info_type = ? AND year = ? AND {month_matches_clause()} AND calc_date = ?
            """,
            (info_type, year, month, month, calc_date),
        ).fetchone()
    if not row:
        return None
    return {
        "t_1_value": row["t_1_value"],
        "t_2_value": row["t_2_value"],
        "mean_value": row["mean_value"],
        "min_value": row["min_value"],
        "max_value": row["max_value"],
        "std_value": row["std_value"],
    }


def get_latest_cached_data(info_type: str, year: int, month: Optional[str], calc_date: str) -> Optional[dict]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, 
            f"""
            SELECT t_1_value, t_2_value, mean_value, min_value, max_value, std_value
            FROM calculated_data
            WHERE info_type = ? AND year = ? AND {month_matches_clause()} AND calc_date <= ?
            ORDER BY calc_date DESC
            LIMIT 1
            """,
            (info_type, year, month, month, calc_date),
        ).fetchone()
    if not row:
        return None
    return {
        "t_1_value": row["t_1_value"],
        "t_2_value": row["t_2_value"],
        "mean_value": row["mean_value"],
        "min_value": row["min_value"],
        "max_value": row["max_value"],
        "std_value": row["std_value"],
    }


def is_data_valid(info_type: str, year: int, month: Optional[str], calc_date: str) -> bool:
    cached = get_cached_data(info_type, year, month, calc_date)
    return bool(cached and cached["t_1_value"] is not None)


def save_calculated_data(
    info_type: str,
    year: int,
    month: Optional[str],
    calc_date: str,
    t_1_value: Optional[float] = None,
    t_2_value: Optional[float] = None,
    mean_value: Optional[float] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    std_value: Optional[float] = None,
) -> None:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, 
            """
            INSERT INTO calculated_data
                (info_type, year, month, calc_date, t_1_value, t_2_value,
                 mean_value, min_value, max_value, std_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (info_type, year, month, calc_date) DO UPDATE SET
                t_1_value = EXCLUDED.t_1_value,
                t_2_value = EXCLUDED.t_2_value,
                mean_value = EXCLUDED.mean_value,
                min_value = EXCLUDED.min_value,
                max_value = EXCLUDED.max_value,
                std_value = EXCLUDED.std_value,
                created_at = EXCLUDED.created_at
            """,
            (
                info_type,
                year,
                month,
                calc_date,
                t_1_value,
                t_2_value,
                mean_value,
                min_value,
                max_value,
                std_value,
            ),
        )


def save_daily_prices_batch(price_list: Iterable[tuple[str, str, str, Optional[float]]]) -> None:
    with db.connect() as conn:
        cur = conn.cursor()
        db._executemany(cur, 
            """
            INSERT INTO daily_prices
                (info_type, contract_code, calc_date, close_price)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (info_type, contract_code, calc_date) DO UPDATE SET
                close_price = EXCLUDED.close_price
            """,
            list(price_list),
        )


def get_all_prices_for_info_type(info_type: str) -> Optional[dict]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT contract_code, calc_date, close_price
            FROM daily_prices
            WHERE info_type = ?
            ORDER BY contract_code, calc_date ASC
            """,
            (info_type,),
        ).fetchall()
    if not rows:
        return None
    result = {}
    for row in rows:
        result.setdefault(row["contract_code"], {})[row["calc_date"]] = row["close_price"]
    return result


def get_prices_for_info_contracts(info_type: str, contract_codes: list[str]) -> Optional[dict]:
    if not contract_codes:
        return None
    placeholders = ", ".join([db._q()] * len(contract_codes))
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur,
            f"""
            SELECT contract_code, calc_date, close_price
            FROM daily_prices
            WHERE info_type = ? AND contract_code IN ({placeholders})
            ORDER BY contract_code, calc_date ASC
            """,
            (info_type, *contract_codes),
        ).fetchall()
    if not rows:
        return None
    result = {}
    for row in rows:
        result.setdefault(row["contract_code"], {})[row["calc_date"]] = row["close_price"]
    return result


def get_all_prices_for_contract(contract_code: str) -> Optional[dict]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT calc_date, close_price
            FROM daily_prices
            WHERE UPPER(contract_code) = UPPER(?)
            ORDER BY calc_date ASC
            """,
            (contract_code,),
        ).fetchall()
    if not rows:
        return None
    return {row["calc_date"]: row["close_price"] for row in rows}


def get_trading_days_before(date_str: str, count: int) -> list[str]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT date
            FROM trading_days
            WHERE date < ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (date_str, count),
        ).fetchall()
    return [row["date"] for row in reversed(rows)]


def cache_counts() -> dict:
    with db.connect() as conn:
        cur = conn.cursor()
        return {
            "calculated_data": db._exec(cur, "SELECT COUNT(*) AS cnt FROM calculated_data").fetchone()["cnt"],
            "daily_prices": db._exec(cur, "SELECT COUNT(*) AS cnt FROM daily_prices").fetchone()["cnt"],
            "trading_days": db._exec(cur, "SELECT COUNT(*) AS cnt FROM trading_days").fetchone()["cnt"],
        }


def import_desktop_cache(zip_path: Path = DESKTOP_SOURCE_ZIP) -> dict:
    if not zip_path.exists():
        raise FileNotFoundError(f"未找到旧源码包：{zip_path}")

    with zipfile.ZipFile(zip_path) as archive:
        cache_name = next(
            (name for name in archive.namelist() if name.endswith(DESKTOP_CACHE_SUFFIX)),
            None,
        )
        if not cache_name:
            raise FileNotFoundError("旧源码包中未找到 modules/info_alert/data_cache.db")
        with tempfile.NamedTemporaryFile(suffix=".db") as temp_db:
            temp_db.write(archive.read(cache_name))
            temp_db.flush()
            return import_cache_db(Path(temp_db.name))


def import_cache_db(source_db: Path) -> dict:
    before = cache_counts()
    src = sqlite3.connect(source_db)
    src.row_factory = sqlite3.Row
    try:
        calculated_rows = src.execute(
            """
            SELECT info_type, year, month, calc_date, t_1_value, t_2_value,
                   mean_value, min_value, max_value, std_value, created_at
            FROM calculated_data
            """
        ).fetchall()
        price_rows = src.execute(
            """
            SELECT info_type, contract_code, calc_date, close_price
            FROM daily_prices
            """
        ).fetchall()
        trading_rows = src.execute("SELECT date FROM trading_days").fetchall()
    finally:
        src.close()

    with db.connect() as conn:
        cur = conn.cursor()
        db._executemany(cur, 
            """
            INSERT INTO calculated_data
                (info_type, year, month, calc_date, t_1_value, t_2_value,
                 mean_value, min_value, max_value, std_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (info_type, year, month, calc_date) DO UPDATE SET
                t_1_value = EXCLUDED.t_1_value,
                t_2_value = EXCLUDED.t_2_value,
                mean_value = EXCLUDED.mean_value,
                min_value = EXCLUDED.min_value,
                max_value = EXCLUDED.max_value,
                std_value = EXCLUDED.std_value,
                created_at = EXCLUDED.created_at
            """,
            [
                (
                    row["info_type"],
                    row["year"],
                    row["month"],
                    row["calc_date"],
                    row["t_1_value"],
                    row["t_2_value"],
                    row["mean_value"],
                    row["min_value"],
                    row["max_value"],
                    row["std_value"],
                    row["created_at"],
                )
                for row in calculated_rows
            ],
        )
        db._executemany(cur, 
            """
            INSERT INTO daily_prices
                (info_type, contract_code, calc_date, close_price)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (info_type, contract_code, calc_date) DO UPDATE SET
                close_price = EXCLUDED.close_price
            """,
            [
                (row["info_type"], row["contract_code"], row["calc_date"], row["close_price"])
                for row in price_rows
            ],
        )
        db._executemany(cur, 
            "INSERT INTO trading_days (date) VALUES (?) ON CONFLICT (date) DO NOTHING",
            [(row["date"],) for row in trading_rows],
        )

    after = cache_counts()
    return {
        "before": before,
        "after": after,
        "source": {
            "calculated_data": len(calculated_rows),
            "daily_prices": len(price_rows),
            "trading_days": len(trading_rows),
        },
    }


def get_existing_calculated_dates(info_type: str, year: int, month: str) -> set:
    """返回 calculated_data 中已存在的 calc_date 集合，用于增量计算判断。"""
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur,
            """
            SELECT calc_date FROM calculated_data
            WHERE info_type = ? AND year = ? AND month = ?
            """,
            (info_type, year, month),
        ).fetchall()
    return {row["calc_date"] for row in rows}


def delete_old_daily_prices(before_date: str) -> int:
    """删除 daily_prices 中早于 before_date 的行，返回删除数。"""
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "DELETE FROM daily_prices WHERE calc_date < ?", (before_date,))
        db._exec(cur, "SELECT changes()")
        return cur.fetchone()[0]


def delete_old_calculated_data(before_date: str) -> int:
    """删除 calculated_data 中早于 before_date 的行，返回删除数。"""
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "DELETE FROM calculated_data WHERE calc_date < ?", (before_date,))
        db._exec(cur, "SELECT changes()")
        return cur.fetchone()[0]
