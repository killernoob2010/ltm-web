"""交易总览的只读汇总查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from . import db


OVERVIEW_SCOPES = {"all", "basic_hedging", "strategic_hedging"}


@dataclass
class OverviewFilters:
    account_id: Optional[int] = None
    scope: str = "all"
    start_date: str = ""
    end_date: str = ""

    def __post_init__(self) -> None:
        if self.scope not in OVERVIEW_SCOPES:
            raise ValueError("未知总览统计范围")
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError("开始日期和结束日期必须同时提供")
        for value in (self.start_date, self.end_date):
            if not value:
                continue
            try:
                datetime.strptime(value, "%Y%m%d")
            except ValueError as exc:
                raise ValueError("日期格式必须为 YYYYMMDD") from exc
        if self.start_date and self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")


def latest_overview_date(account_id: Optional[int] = None) -> Optional[str]:
    account_filter = int(account_id or 0)
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            """
            SELECT MAX(business_date) AS business_date
            FROM (
                SELECT tf.trade_date AS business_date
                FROM trading_trade_facts tf
                JOIN trading_import_batches b
                  ON b.id = tf.batch_id AND b.status = 'active'
                WHERE tf.is_current = 1
                  AND (? = 0 OR b.account_id = ?)
                UNION ALL
                SELECT cf.close_date AS business_date
                FROM trading_close_facts cf
                JOIN trading_import_batches b
                  ON b.id = cf.batch_id AND b.status = 'active'
                WHERE cf.is_current = 1
                  AND (? = 0 OR b.account_id = ?)
                UNION ALL
                SELECT ps.snapshot_date AS business_date
                FROM trading_position_snapshots ps
                JOIN trading_import_batches b
                  ON b.id = ps.batch_id AND b.status = 'active'
                WHERE ps.is_current = 1
                  AND (? = 0 OR b.account_id = ?)
            ) available_dates
            """,
            (account_filter, account_filter) * 3,
        ).fetchone()
    return row["business_date"] if row else None


def _account_value(account_id: Optional[int]) -> int:
    return int(account_id or 0)


def _filter_payload(filters: OverviewFilters) -> dict[str, Any]:
    labels = {
        "all": "全部",
        "basic_hedging": "基础套保",
        "strategic_hedging": "战略套保",
    }
    return {
        "account_id": filters.account_id,
        "scope": filters.scope,
        "scope_label": labels[filters.scope],
        "pnl_metric": "fact_pnl" if filters.scope == "all" else "business_pnl",
        "start_date": filters.start_date,
        "end_date": filters.end_date,
    }


def _query_quality(cur, filters: OverviewFilters) -> dict[str, int]:
    account_id = _account_value(filters.account_id)
    row = db._exec(
        cur,
        """
        SELECT
            (SELECT COUNT(*)
             FROM trading_trade_facts tf
             JOIN trading_import_batches b
               ON b.id = tf.batch_id AND b.status = 'active'
             LEFT JOIN trading_business_assignments ba
               ON ba.trade_identity_id = tf.identity_id
             WHERE tf.is_current = 1 AND tf.open_close = '开仓'
               AND ba.id IS NULL
               AND (? = 0 OR b.account_id = ?)
               AND (? = '' OR tf.trade_date >= ?)
               AND (? = '' OR tf.trade_date <= ?)) AS unassigned_trade_count,
            (SELECT COUNT(*)
             FROM trading_close_facts cf
             JOIN trading_import_batches b
               ON b.id = cf.batch_id AND b.status = 'active'
             WHERE cf.is_current = 1
               AND (? = 0 OR b.account_id = ?)
               AND (? = '' OR cf.close_date >= ?)
               AND (? = '' OR cf.close_date <= ?)) AS close_record_count,
            (SELECT COUNT(*)
             FROM trading_close_facts cf
             JOIN trading_import_batches b
               ON b.id = cf.batch_id AND b.status = 'active'
             WHERE cf.is_current = 1
               AND NOT EXISTS (
                   SELECT 1 FROM trading_business_close_allocations a
                   WHERE a.close_identity_id = cf.identity_id
               )
               AND (? = 0 OR b.account_id = ?)
               AND (? = '' OR cf.close_date >= ?)
               AND (? = '' OR cf.close_date <= ?)) AS unallocated_close_count,
            CASE WHEN ? = 'all' THEN (
                SELECT COUNT(DISTINCT account_id)
                FROM (
                    SELECT b.account_id
                    FROM trading_trade_facts tf
                    JOIN trading_import_batches b
                      ON b.id = tf.batch_id AND b.status = 'active'
                    WHERE tf.is_current = 1
                      AND (? = 0 OR b.account_id = ?)
                      AND (? = '' OR tf.trade_date <= ?)
                    UNION
                    SELECT b.account_id
                    FROM trading_close_facts cf
                    JOIN trading_import_batches b
                      ON b.id = cf.batch_id AND b.status = 'active'
                    WHERE cf.is_current = 1
                      AND (? = 0 OR b.account_id = ?)
                      AND (? = '' OR cf.close_date <= ?)
                    UNION
                    SELECT b.account_id
                    FROM trading_position_snapshots ps
                    JOIN trading_import_batches b
                      ON b.id = ps.batch_id AND b.status = 'active'
                    WHERE ps.is_current = 1
                      AND (? = 0 OR b.account_id = ?)
                      AND (? = '' OR ps.snapshot_date <= ?)
                ) applicable_fact_accounts
            ) ELSE (
                SELECT COUNT(DISTINCT b.account_id)
                FROM trading_trade_facts tf
                JOIN trading_import_batches b
                  ON b.id = tf.batch_id AND b.status = 'active'
                JOIN trading_business_assignments ba
                  ON ba.trade_identity_id = tf.identity_id
                WHERE tf.is_current = 1 AND tf.open_close = '开仓'
                  AND ba.business_type = ?
                  AND (? = 0 OR b.account_id = ?)
                  AND (? = '' OR tf.trade_date <= ?)
            ) END AS applicable_account_count
        """,
        (
            account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
            account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
            account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
            filters.scope,
            account_id, account_id, filters.end_date, filters.end_date,
            account_id, account_id, filters.end_date, filters.end_date,
            account_id, account_id, filters.end_date, filters.end_date,
            filters.scope, account_id, account_id,
            filters.end_date, filters.end_date,
        ),
    ).fetchone()
    return {
        "unassigned_trade_count": int(row["unassigned_trade_count"] or 0),
        "close_record_count": int(row["close_record_count"] or 0),
        "unallocated_close_count": int(row["unallocated_close_count"] or 0),
        "applicable_account_count": int(row["applicable_account_count"] or 0),
    }


def _query_snapshot_groups(cur, filters: OverviewFilters) -> list[dict[str, Any]]:
    account_id = _account_value(filters.account_id)
    rows = db._exec(
        cur,
        """
        WITH latest_snapshots AS (
            SELECT b.account_id, MAX(ps.snapshot_date) AS snapshot_date
            FROM trading_position_snapshots ps
            JOIN trading_import_batches b
              ON b.id = ps.batch_id AND b.status = 'active'
            WHERE ps.is_current = 1
              AND (? = 0 OR b.account_id = ?)
              AND (? = '' OR ps.snapshot_date <= ?)
              AND (
                  ? = 'all'
                  OR EXISTS (
                      SELECT 1
                      FROM trading_trade_facts tf
                      JOIN trading_business_assignments ba
                        ON ba.trade_identity_id = tf.identity_id
                      JOIN trading_import_batches tb
                        ON tb.id = tf.batch_id AND tb.status = 'active'
                      WHERE tf.is_current = 1 AND tf.open_close = '开仓'
                        AND tb.account_id = b.account_id
                        AND ba.business_type = ?
                        AND (? = '' OR tf.trade_date <= ?)
                  )
              )
            GROUP BY b.account_id
        )
        SELECT b.account_id, ps.snapshot_date, ps.contract, ps.direction,
               ps.asset_type, COALESCE(SUM(ps.quantity), 0) AS quantity,
               COALESCE(SUM(ps.margin), 0) AS margin
        FROM trading_position_snapshots ps
        JOIN trading_import_batches b
          ON b.id = ps.batch_id AND b.status = 'active'
        JOIN latest_snapshots latest
          ON latest.account_id = b.account_id
         AND latest.snapshot_date = ps.snapshot_date
        WHERE ps.is_current = 1
        GROUP BY b.account_id, ps.snapshot_date, ps.contract, ps.direction,
                 ps.asset_type
        ORDER BY b.account_id, ps.contract, ps.direction, ps.asset_type
        """,
        (
            account_id, account_id, filters.end_date, filters.end_date,
            filters.scope, filters.scope, filters.end_date, filters.end_date,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _snapshot_metadata(
    snapshot_rows: list[dict[str, Any]],
    applicable_account_count: int,
) -> tuple[list[str], str, int]:
    snapshot_accounts = {int(row["account_id"]) for row in snapshot_rows}
    snapshot_dates = sorted({row["snapshot_date"] for row in snapshot_rows})
    missing_count = max(0, applicable_account_count - len(snapshot_accounts))
    if missing_count and snapshot_accounts:
        status = "partial"
    elif missing_count or not snapshot_accounts:
        status = "missing"
    elif len(snapshot_dates) > 1:
        status = "mixed"
    else:
        status = "ok"
    return snapshot_dates, status, missing_count


def _fact_overview(cur, filters: OverviewFilters) -> dict[str, Any]:
    account_id = _account_value(filters.account_id)
    trade_row = db._exec(
        cur,
        """
        SELECT COUNT(*) AS record_count,
               COALESCE(SUM(tf.quantity), 0) AS quantity,
               COALESCE(SUM(tf.fee), 0) AS fee
        FROM trading_trade_facts tf
        JOIN trading_import_batches b
          ON b.id = tf.batch_id AND b.status = 'active'
        WHERE tf.is_current = 1
          AND (? = 0 OR b.account_id = ?)
          AND (? = '' OR tf.trade_date >= ?)
          AND (? = '' OR tf.trade_date <= ?)
        """,
        (
            account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
        ),
    ).fetchone()
    daily_rows = db._exec(
        cur,
        """
        SELECT cf.close_date AS date,
               COALESCE(SUM(cf.fact_close_pnl), 0) AS value
        FROM trading_close_facts cf
        JOIN trading_import_batches b
          ON b.id = cf.batch_id AND b.status = 'active'
        WHERE cf.is_current = 1
          AND (? = 0 OR b.account_id = ?)
          AND (? = '' OR cf.close_date >= ?)
          AND (? = '' OR cf.close_date <= ?)
        GROUP BY cf.close_date
        ORDER BY cf.close_date
        """,
        (
            account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
        ),
    ).fetchall()
    quality = _query_quality(cur, filters)
    snapshot_rows = _query_snapshot_groups(cur, filters)
    snapshot_dates, snapshot_status, missing_count = _snapshot_metadata(
        snapshot_rows, quality.pop("applicable_account_count")
    )
    available = snapshot_status not in {"missing", "partial"}
    nonzero_rows = [
        row for row in snapshot_rows if abs(float(row["quantity"] or 0)) > 1e-9
    ]
    daily_pnl = [
        {"date": row["date"], "value": float(row["value"] or 0)}
        for row in daily_rows
    ]
    quality.update({
        "missing_snapshot_account_count": missing_count,
        "unmatched_business_position_count": 0,
        "missing_business_pnl_count": 0,
    })
    return {
        "filters": _filter_payload(filters),
        "trades": {
            "record_count": int(trade_row["record_count"] or 0),
            "quantity": float(trade_row["quantity"] or 0),
            "fee": float(trade_row["fee"] or 0),
        },
        "pnl": {
            "value": sum(row["value"] for row in daily_pnl),
            "metric": "fact_pnl",
        },
        "positions": {
            "group_count": len(nonzero_rows) if available else None,
            "quantity": (
                sum(float(row["quantity"] or 0) for row in nonzero_rows)
                if available else None
            ),
            "margin": (
                sum(float(row["margin"] or 0) for row in snapshot_rows)
                if available else None
            ),
            "snapshot_status": snapshot_status,
            "snapshot_dates": snapshot_dates,
        },
        "daily_pnl": daily_pnl,
        "data_quality": quality,
    }


def _business_period_metrics(
    cur, filters: OverviewFilters
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    account_id = _account_value(filters.account_id)
    rows = db._exec(
        cur,
        """
        WITH attributed_trades AS (
            SELECT tf.identity_id, tf.quantity AS quantity,
                   COALESCE(tf.fee, 0) AS fee
            FROM trading_trade_facts tf
            JOIN trading_import_batches b
              ON b.id = tf.batch_id AND b.status = 'active'
            JOIN trading_business_assignments ba
              ON ba.trade_identity_id = tf.identity_id
            WHERE tf.is_current = 1 AND tf.open_close = '开仓'
              AND ba.business_type = ?
              AND (? = 0 OR b.account_id = ?)
              AND (? = '' OR tf.trade_date >= ?)
              AND (? = '' OR tf.trade_date <= ?)
            UNION ALL
            SELECT tf.identity_id,
                   l.matched_quantity * a.matched_quantity
                     / NULLIF(cf.quantity, 0) AS quantity,
                   COALESCE(tf.fee, 0)
                     * l.matched_quantity / NULLIF(tf.quantity, 0)
                     * a.matched_quantity / NULLIF(cf.quantity, 0) AS fee
            FROM trading_trade_facts tf
            JOIN trading_import_batches b
              ON b.id = tf.batch_id AND b.status = 'active'
            JOIN trading_close_trade_links l
              ON l.close_trade_identity_id = tf.identity_id
            JOIN trading_close_facts cf
              ON cf.identity_id = l.close_identity_id AND cf.is_current = 1
            JOIN trading_import_batches cb
              ON cb.id = cf.batch_id AND cb.status = 'active'
            JOIN trading_business_close_allocations a
              ON a.close_identity_id = cf.identity_id
            JOIN trading_business_assignments ba
              ON ba.trade_identity_id = a.open_trade_identity_id
            WHERE tf.is_current = 1 AND tf.open_close <> '开仓'
              AND ba.business_type = ?
              AND (? = 0 OR b.account_id = ?)
              AND (? = '' OR tf.trade_date >= ?)
              AND (? = '' OR tf.trade_date <= ?)
        ),
        daily_business_pnl AS (
            SELECT cf.close_date AS date,
                   SUM(a.business_pnl) AS value,
                   SUM(CASE WHEN a.business_pnl IS NULL THEN 1 ELSE 0 END)
                       AS missing_count
            FROM trading_close_facts cf
            JOIN trading_import_batches b
              ON b.id = cf.batch_id AND b.status = 'active'
            JOIN trading_business_close_allocations a
              ON a.close_identity_id = cf.identity_id
            JOIN trading_business_assignments ba
              ON ba.trade_identity_id = a.open_trade_identity_id
            WHERE cf.is_current = 1 AND ba.business_type = ?
              AND (? = 0 OR b.account_id = ?)
              AND (? = '' OR cf.close_date >= ?)
              AND (? = '' OR cf.close_date <= ?)
            GROUP BY cf.close_date
        )
        SELECT 'summary' AS row_kind, NULL AS date,
               COUNT(DISTINCT identity_id) AS record_count,
               COALESCE(SUM(quantity), 0) AS quantity,
               COALESCE(SUM(fee), 0) AS fee,
               NULL AS value, 0 AS missing_count
        FROM attributed_trades
        UNION ALL
        SELECT 'daily' AS row_kind, date, 0 AS record_count,
               0 AS quantity, 0 AS fee, value, missing_count
        FROM daily_business_pnl
        """,
        (
            filters.scope, account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
            filters.scope, account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
            filters.scope, account_id, account_id,
            filters.start_date, filters.start_date,
            filters.end_date, filters.end_date,
        ),
    ).fetchall()
    summary = next(row for row in rows if row["row_kind"] == "summary")
    daily_rows = sorted(
        (row for row in rows if row["row_kind"] == "daily"),
        key=lambda row: row["date"],
    )
    missing_count = sum(int(row["missing_count"] or 0) for row in daily_rows)
    trades = {
        "record_count": int(summary["record_count"] or 0),
        "quantity": float(summary["quantity"] or 0),
        "fee": float(summary["fee"] or 0),
    }
    daily = [
        {
            "date": row["date"],
            "value": float(row["value"] or 0) if not row["missing_count"] else None,
        }
        for row in daily_rows
    ]
    return trades, daily, missing_count


def _business_position_groups(cur, filters: OverviewFilters) -> list[dict[str, Any]]:
    account_id = _account_value(filters.account_id)
    rows = db._exec(
        cur,
        """
        WITH allocated_quantities AS (
            SELECT a.open_trade_identity_id,
                   SUM(a.matched_quantity) AS matched_quantity
            FROM trading_business_close_allocations a
            JOIN trading_close_facts cf
              ON cf.identity_id = a.close_identity_id AND cf.is_current = 1
            JOIN trading_import_batches cb
              ON cb.id = cf.batch_id AND cb.status = 'active'
            WHERE (? = '' OR cf.close_date <= ?)
            GROUP BY a.open_trade_identity_id
        )
        SELECT b.account_id, tf.contract, tf.side AS direction, tf.asset_type,
               SUM(tf.quantity - COALESCE(aq.matched_quantity, 0))
                   AS remaining_quantity
        FROM trading_trade_facts tf
        JOIN trading_import_batches b
          ON b.id = tf.batch_id AND b.status = 'active'
        JOIN trading_business_assignments ba
          ON ba.trade_identity_id = tf.identity_id
        LEFT JOIN allocated_quantities aq
          ON aq.open_trade_identity_id = tf.identity_id
        WHERE tf.is_current = 1 AND tf.open_close = '开仓'
          AND ba.business_type = ?
          AND (? = 0 OR b.account_id = ?)
          AND (? = '' OR tf.trade_date <= ?)
        GROUP BY b.account_id, tf.contract, tf.side, tf.asset_type
        HAVING SUM(tf.quantity - COALESCE(aq.matched_quantity, 0)) > 0
        ORDER BY b.account_id, tf.contract, tf.side, tf.asset_type
        """,
        (
            filters.end_date, filters.end_date, filters.scope,
            account_id, account_id, filters.end_date, filters.end_date,
        ),
    ).fetchall()
    return [
        {
            "account_id": int(row["account_id"]),
            "contract": row["contract"],
            "direction": row["direction"],
            "asset_type": row["asset_type"],
            "quantity": float(row["remaining_quantity"] or 0),
        }
        for row in rows
    ]


def _business_overview(cur, filters: OverviewFilters) -> dict[str, Any]:
    trades, daily_pnl, missing_business_pnl_count = _business_period_metrics(
        cur, filters
    )
    business_positions = _business_position_groups(cur, filters)
    quality = _query_quality(cur, filters)
    snapshot_rows = _query_snapshot_groups(cur, filters)
    snapshot_dates, snapshot_status, missing_snapshot_count = _snapshot_metadata(
        snapshot_rows, quality.pop("applicable_account_count")
    )
    snapshot_by_key = {
        (
            int(row["account_id"]), row["contract"], row["direction"],
            row["asset_type"],
        ): row
        for row in snapshot_rows
    }
    margin = 0.0
    unmatched_count = 0
    for position in business_positions:
        key = (
            position["account_id"], position["contract"], position["direction"],
            position["asset_type"],
        )
        snapshot = snapshot_by_key.get(key)
        snapshot_quantity = float(snapshot["quantity"] or 0) if snapshot else 0.0
        if (
            snapshot is None
            or snapshot_quantity <= 1e-9
            or position["quantity"] - snapshot_quantity > 1e-9
        ):
            unmatched_count += 1
            continue
        margin += (
            float(snapshot["margin"] or 0)
            * float(position["quantity"])
            / snapshot_quantity
        )
    positions_available = (
        snapshot_status not in {"missing", "partial"} and unmatched_count == 0
    )
    quality.update({
        "missing_snapshot_account_count": missing_snapshot_count,
        "unmatched_business_position_count": unmatched_count,
        "missing_business_pnl_count": missing_business_pnl_count,
    })
    pnl_available = missing_business_pnl_count == 0
    return {
        "filters": _filter_payload(filters),
        "trades": trades,
        "pnl": {
            "value": (
                sum(float(row["value"] or 0) for row in daily_pnl)
                if pnl_available else None
            ),
            "metric": "business_pnl",
        },
        "positions": {
            "group_count": len(business_positions) if positions_available else None,
            "quantity": (
                sum(row["quantity"] for row in business_positions)
                if positions_available else None
            ),
            "margin": margin if positions_available else None,
            "snapshot_status": snapshot_status,
            "snapshot_dates": snapshot_dates,
        },
        "daily_pnl": daily_pnl,
        "data_quality": quality,
    }


def build_trading_overview(filters: OverviewFilters) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        if filters.scope == "all":
            return _fact_overview(cur, filters)
        return _business_overview(cur, filters)
