import hashlib
import hmac
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import psycopg2
import psycopg2.extras
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

MODULES = [
    ("台账管理", "sh_junneng", "上海钧能台账"),
    ("台账管理", "steel_export", "钢材出口套保台账"),
    ("台账管理", "subsidiary_hedging", "子公司套保台账"),
    ("台账管理", "option_trading", "期权交易台账"),
    ("信息预警管理", "info_summary", "实时信息汇总"),
    ("信息预警管理", "risk_alert", "风险预警"),
    ("信息预警管理", "mid_event_monitor", "事中风险监控"),
    ("数据可视化管理", "data_visualization_integration", "数据整合"),
    ("数据可视化管理", "data_visualization_data", "数据管理"),
    ("数据可视化管理", "data_visualization_chart", "数据展示"),
    ("订单融资管理", "order_finance_progress", "进度监控"),
    ("后台管理", "user_management", "用户管理"),
    ("后台管理", "data_management", "数据管理"),
]


def get_db_url() -> str:
    """Return DATABASE_URL from env or fall back to local SQLite for dev."""
    return os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")


def _is_pg() -> bool:
    """True when DATABASE_URL points to PostgreSQL."""
    return get_db_url().startswith("postgres")



def _pg_rewrite(sql: str) -> str:
    """Convert SQLite-specific syntax to PostgreSQL on the fly."""
    if not _is_pg():
        return sql
    # INSERT OR REPLACE -> INSERT ... ON CONFLICT DO UPDATE
    m = re.match(
        r"INSERT OR REPLACE INTO (\w+) \((.+?)\) VALUES \((.+?)\)$",
        sql, re.DOTALL | re.IGNORECASE,
    )
    if m:
        table = m.group(1)
        cols = [c.strip() for c in m.group(2).split(",")]
        vals = m.group(3)
        cols_str = ", ".join(cols)
        set_str = ", ".join(c + "=EXCLUDED." + c for c in cols)
        return "INSERT INTO " + table + " (" + cols_str + ") VALUES (" + vals + ") ON CONFLICT (" + cols_str + ") DO UPDATE SET " + set_str
    # INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    m2 = re.match(
        r"INSERT OR IGNORE INTO (\w+) .*", sql, re.DOTALL | re.IGNORECASE,
    )
    if m2:
        return re.sub(r"INSERT OR IGNORE", "INSERT", sql, flags=re.IGNORECASE) + " ON CONFLICT DO NOTHING"
    return sql

PBKDF2_ITERATIONS = 260_000


def legacy_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = stored_hash.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(digest, expected)
    return hmac.compare_digest(legacy_password_hash(password), stored_hash)


def needs_password_upgrade(stored_hash: str) -> bool:
    return not (stored_hash or "").startswith("pbkdf2_sha256$")


def upgrade_user_password(user_id: int, password: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        _exec(cur, "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (password_hash(password), user_id))


def _q() -> str:
    """Returns the right placeholder ('%s' or '?') for the current DB."""
    return "%s" if _is_pg() else "?"


@contextmanager
def connect():
    """Return a DB-API 2.0 connection (psycopg2 for PG, sqlite3 for SQLite fallback)."""
    db_url = get_db_url()
    if db_url.startswith("postgres"):
        conn = psycopg2.connect(db_url, connect_timeout=30)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
    else:
        # SQLite fallback
        import sqlite3
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _last_ids.pop(id(conn), None)
        conn.close()



_last_ids = {}  # id(conn) -> int, for tracking last insert id across PG and SQLite


def _exec(cur, sql, params=None):
    """Execute SQL with correct placeholder style and PG syntax rewrite. Returns cur for chaining."""
    global _last_ids
    sql_orig = sql
    sql = _pg_rewrite(sql)
    is_insert = sql_orig.strip().upper().startswith("INSERT")
    if is_insert:
        if _is_pg():
            sql = sql.replace("?", "%s")
            if "RETURNING" not in sql.upper():
                sql = sql + " RETURNING id"
            if params is None:
                cur.execute(sql)
            else:
                cur.execute(sql, params)
            row = cur.fetchone()
            _last_ids[id(cur.connection)] = row["id"] if row else None
        else:
            # SQLite
            if params is None:
                cur.execute(sql)
            else:
                cur.execute(sql, params)
            _last_ids[id(cur.connection)] = cur.lastrowid
        return cur
    if _is_pg():
        sql = sql.replace("?", "%s")
    if params is None:
        cur.execute(sql)
    else:
        cur.execute(sql, params)
    return cur


def last_insert_id(conn) -> int:
    """Return the most recent INSERT id for this connection."""
    return _last_ids.get(id(conn))


def _executemany(cur, sql, seq):
    """Execute many with correct placeholder style and PG syntax rewrite."""
    sql = _pg_rewrite(sql)
    if _is_pg():
        sql = sql.replace("?", "%s")
    cur.executemany(sql, seq)


def _last_insert_id(cur, sql, params) -> int:
    """Execute INSERT and return the new row id (PG uses RETURNING id)."""
    if _is_pg():
        # If sql already has RETURNING, just execute
        if "RETURNING" in sql.upper():
            cur.execute(sql.replace("?", "%s"), params)
            return cur.fetchone()["id"]
        # Otherwise append RETURNING id
        sql = sql.replace("?", "%s") + " RETURNING id"
        cur.execute(sql, params)
        return cur.fetchone()["id"]
    cur.execute(sql, params)
    return cur.lastrowid


def init_db() -> None:
    with connect() as conn:
        cur = conn.cursor()
        if _is_pg():
            cur.execute(
                """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                department TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '启用',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS module_permissions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                module_code TEXT NOT NULL,
                can_view INTEGER NOT NULL DEFAULT 1,
                can_edit INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, module_code),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                login_time TEXT DEFAULT CURRENT_TIMESTAMP,
                last_activity TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT '活跃',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS operation_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                module_code TEXT,
                entity_type TEXT,
                entity_id INTEGER,
                operation_type TEXT NOT NULL,
                description TEXT NOT NULL,
                before_data TEXT,
                after_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alert_settings (
                id SERIAL PRIMARY KEY,
                info_type TEXT NOT NULL,
                contract_year TEXT NOT NULL DEFAULT '2026',
                contract_month TEXT NOT NULL,
                alert_value DOUBLE PRECISION NOT NULL,
                direction TEXT NOT NULL DEFAULT 'above',
                status TEXT NOT NULL DEFAULT 'enabled',
                creator TEXT,
                reminder_users TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                id SERIAL PRIMARY KEY,
                alert_id INTEGER,
                alert_time TEXT DEFAULT CURRENT_TIMESTAMP,
                current_value DOUBLE PRECISION,
                alert_value DOUBLE PRECISION,
                direction TEXT,
                status TEXT NOT NULL DEFAULT 'unread',
                FOREIGN KEY (alert_id) REFERENCES alert_settings(id)
            );

            CREATE TABLE IF NOT EXISTS calculated_data (
                id SERIAL PRIMARY KEY,
                info_type TEXT NOT NULL,
                year INTEGER NOT NULL,
                month TEXT,
                calc_date TEXT NOT NULL,
                t_1_value DOUBLE PRECISION,
                t_2_value DOUBLE PRECISION,
                mean_value DOUBLE PRECISION,
                min_value DOUBLE PRECISION,
                max_value DOUBLE PRECISION,
                std_value DOUBLE PRECISION,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(info_type, year, month, calc_date)
            );
            CREATE TABLE IF NOT EXISTS dv_week_keys (
                id SERIAL PRIMARY KEY,
                year INTEGER NOT NULL,
                week_no INTEGER NOT NULL,
                week_start_date TEXT NOT NULL,
                week_end_date TEXT NOT NULL,
                shipment_date TEXT,
                inventory_date TEXT,
                display_date TEXT NOT NULL,
                UNIQUE(year, week_no, shipment_date, inventory_date)
            );

            CREATE TABLE IF NOT EXISTS dv_data_points (
                id SERIAL PRIMARY KEY,
                week_key_id INTEGER NOT NULL,
                product TEXT NOT NULL DEFAULT '卡粉',
                metric_type TEXT NOT NULL,
                imported_value DOUBLE PRECISION,
                calculated_value DOUBLE PRECISION,
                manual_value DOUBLE PRECISION,
                display_value DOUBLE PRECISION,
                is_manual_override INTEGER NOT NULL DEFAULT 0,
                is_missing_filled INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '导入',
                source_batch_id INTEGER,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(week_key_id, product, metric_type),
                FOREIGN KEY (week_key_id) REFERENCES dv_week_keys(id)
            );

            CREATE TABLE IF NOT EXISTS dv_import_batches (
                id SERIAL PRIMARY KEY,
                file_name TEXT NOT NULL,
                metric_types TEXT NOT NULL,
                date_start TEXT,
                date_end TEXT,
                insert_count INTEGER NOT NULL DEFAULT 0,
                overwrite_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                manual_protected_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dv_change_log (
                id SERIAL PRIMARY KEY,
                data_point_id INTEGER NOT NULL,
                old_value DOUBLE PRECISION,
                new_value DOUBLE PRECISION,
                operation_type TEXT NOT NULL,
                source_batch_id INTEGER,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                note TEXT,
                FOREIGN KEY (data_point_id) REFERENCES dv_data_points(id)
            );

            CREATE TABLE IF NOT EXISTS dv_integration_batches (
                id SERIAL PRIMARY KEY,
                file_names TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                point_count INTEGER NOT NULL DEFAULT 0,
                apparent_demand_count INTEGER NOT NULL DEFAULT 0,
                validation_summary TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dv_integrated_points (
                id SERIAL PRIMARY KEY,
                batch_id INTEGER,
                week_start TEXT NOT NULL,
                week_end TEXT,
                business_year INTEGER,
                business_week INTEGER,
                week_label TEXT,
                display_date TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                source_country TEXT NOT NULL,
                product TEXT NOT NULL,
                category TEXT NOT NULL,
                mainstream_status TEXT NOT NULL,
                value DOUBLE PRECISION,
                unit TEXT NOT NULL DEFAULT '万吨',
                source_file TEXT,
                source_sheet TEXT,
                source_section TEXT,
                is_calculable INTEGER NOT NULL DEFAULT 0,
                validation_status TEXT,
                note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES dv_integration_batches(id)
            );

            CREATE TABLE IF NOT EXISTS order_finance_progress (
                id SERIAL PRIMARY KEY,
                business_key TEXT NOT NULL UNIQUE,
                subsidiary TEXT NOT NULL,
                source_file TEXT,
                source_sheet TEXT,
                source_row_start INTEGER,
                source_row_end INTEGER,
                source_snapshot_date TEXT,
                product_name TEXT,
                purchase_contract_no TEXT,
                system_contract_no TEXT,
                buyer TEXT,
                seller TEXT,
                overseas_entity TEXT,
                terminal_customer TEXT,
                contract_date TEXT,
                trade_term TEXT,
                origin_port TEXT,
                destination_port TEXT,
                contract_quantity_mt DOUBLE PRECISION,
                contract_currency TEXT,
                contract_amount DOUBLE PRECISION,
                finance_bank TEXT,
                finance_amount_expected DOUBLE PRECISION,
                finance_amount_actual DOUBLE PRECISION,
                repaid_amount DOUBLE PRECISION,
                remaining_credit_amount DOUBLE PRECISION,
                finance_drawdown_date TEXT,
                finance_due_date TEXT,
                finance_days INTEGER,
                finance_status TEXT,
                latest_shipment_date TEXT,
                lc_latest_shipment_date TEXT,
                vessel_voyage TEXT,
                bill_of_lading_date TEXT,
                bill_of_lading_no TEXT,
                document_submission_date TEXT,
                collection_date TEXT,
                actual_shipped_quantity_mt DOUBLE PRECISION,
                actual_goods_amount DOUBLE PRECISION,
                tail_amount DOUBLE PRECISION,
                tail_payment_date TEXT,
                executor TEXT,
                business_status TEXT,
                risk_level TEXT,
                planned_drawdown_date TEXT,
                planned_finance_amount DOUBLE PRECISION,
                amount_adjustment_note TEXT,
                repayment_requirement TEXT,
                repayment_requirement_status TEXT,
                next_action TEXT,
                next_follow_up_date TEXT,
                remark TEXT,
                manager_note TEXT,
                manual_override_fields TEXT,
                is_archived INTEGER NOT NULL DEFAULT 0,
                sales_contracts_json TEXT,
                settlement_json TEXT,
                management_plan_json TEXT,
                manual_change_log_json TEXT,
                corrections_json TEXT,
                import_warnings_json TEXT,
                source_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_order_finance_subsidiary
            ON order_finance_progress(subsidiary);

            CREATE INDEX IF NOT EXISTS idx_order_finance_due
            ON order_finance_progress(finance_due_date);

            CREATE INDEX IF NOT EXISTS idx_order_finance_status
            ON order_finance_progress(business_status);


            CREATE TABLE IF NOT EXISTS daily_prices (
                id SERIAL PRIMARY KEY,
                info_type TEXT NOT NULL,
                contract_code TEXT NOT NULL,
                calc_date TEXT NOT NULL,
                close_price DOUBLE PRECISION,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(info_type, contract_code, calc_date)
            );

            CREATE TABLE IF NOT EXISTS trading_days (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS strategy_groups (
                id SERIAL PRIMARY KEY,
                group_name TEXT NOT NULL UNIQUE,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS strategy_positions (
                id SERIAL PRIMARY KEY,
                group_id INTEGER NOT NULL,
                variety TEXT NOT NULL,
                variety_name TEXT,
                direction TEXT NOT NULL,
                open_price DOUBLE PRECISION NOT NULL,
                quantity INTEGER NOT NULL,
                multiplier INTEGER DEFAULT 100,
                contract TEXT DEFAULT '',
                current_price DOUBLE PRECISION,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES strategy_groups(id)
            );

            CREATE TABLE IF NOT EXISTS sh_junneng_trades (
                id SERIAL PRIMARY KEY,
                contract_month TEXT NOT NULL,
                direction TEXT NOT NULL,
                open_price DOUBLE PRECISION NOT NULL,
                open_volume DOUBLE PRECISION,
                current_price DOUBLE PRECISION,
                trade_quantity DOUBLE PRECISION,
                hold_quantity DOUBLE PRECISION,
                open_fee DOUBLE PRECISION DEFAULT 0,
                open_date TEXT,
                close_price DOUBLE PRECISION,
                close_volume DOUBLE PRECISION,
                close_fee DOUBLE PRECISION DEFAULT 0,
                close_date TEXT,
                profit DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT '未平仓',
                is_closed INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sh_junneng_positions (
                id SERIAL PRIMARY KEY,
                source_trade_id INTEGER UNIQUE,
                contract_month TEXT NOT NULL,
                direction TEXT NOT NULL,
                open_price DOUBLE PRECISION NOT NULL,
                open_quantity DOUBLE PRECISION NOT NULL,
                remaining_quantity DOUBLE PRECISION NOT NULL,
                open_amount DOUBLE PRECISION,
                open_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
                open_date TEXT NOT NULL,
                current_price DOUBLE PRECISION,
                business_code TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sh_junneng_close_trades (
                id SERIAL PRIMARY KEY,
                position_id INTEGER NOT NULL,
                close_date TEXT NOT NULL,
                close_quantity DOUBLE PRECISION NOT NULL,
                close_price DOUBLE PRECISION NOT NULL,
                close_amount DOUBLE PRECISION,
                close_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
                open_fee_allocated DOUBLE PRECISION NOT NULL DEFAULT 0,
                close_sequence INTEGER NOT NULL DEFAULT 1,
                business_code TEXT,
                realized_profit DOUBLE PRECISION,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (position_id) REFERENCES sh_junneng_positions(id)
            );
            """
            )
            conn.commit()
        else:
            conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                department TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '启用',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS module_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                module_code TEXT NOT NULL,
                can_view INTEGER NOT NULL DEFAULT 1,
                can_edit INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, module_code),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                login_time TEXT DEFAULT CURRENT_TIMESTAMP,
                last_activity TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT '活跃',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                module_code TEXT,
                entity_type TEXT,
                entity_id INTEGER,
                operation_type TEXT NOT NULL,
                description TEXT NOT NULL,
                before_data TEXT,
                after_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alert_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                info_type TEXT NOT NULL,
                contract_year TEXT NOT NULL DEFAULT '2026',
                contract_month TEXT NOT NULL,
                alert_value REAL NOT NULL,
                direction TEXT NOT NULL DEFAULT 'above',
                status TEXT NOT NULL DEFAULT 'enabled',
                creator TEXT,
                reminder_users TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER,
                alert_time TEXT DEFAULT CURRENT_TIMESTAMP,
                current_value REAL,
                alert_value REAL,
                direction TEXT,
                status TEXT NOT NULL DEFAULT 'unread',
                FOREIGN KEY (alert_id) REFERENCES alert_settings(id)
            );

            CREATE TABLE IF NOT EXISTS calculated_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                info_type TEXT NOT NULL,
                year INTEGER NOT NULL,
                month TEXT,
                calc_date TEXT NOT NULL,
                t_1_value REAL,
                t_2_value REAL,
                mean_value REAL,
                min_value REAL,
                max_value REAL,
                std_value REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(info_type, year, month, calc_date)
            );
            CREATE TABLE IF NOT EXISTS dv_week_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                week_no INTEGER NOT NULL,
                week_start_date TEXT NOT NULL,
                week_end_date TEXT NOT NULL,
                shipment_date TEXT,
                inventory_date TEXT,
                display_date TEXT NOT NULL,
                UNIQUE(year, week_no, shipment_date, inventory_date)
            );

            CREATE TABLE IF NOT EXISTS dv_data_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_key_id INTEGER NOT NULL,
                product TEXT NOT NULL DEFAULT '卡粉',
                metric_type TEXT NOT NULL,
                imported_value REAL,
                calculated_value REAL,
                manual_value REAL,
                display_value REAL,
                is_manual_override INTEGER NOT NULL DEFAULT 0,
                is_missing_filled INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '导入',
                source_batch_id INTEGER,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(week_key_id, product, metric_type),
                FOREIGN KEY (week_key_id) REFERENCES dv_week_keys(id)
            );

            CREATE TABLE IF NOT EXISTS dv_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                metric_types TEXT NOT NULL,
                date_start TEXT,
                date_end TEXT,
                insert_count INTEGER NOT NULL DEFAULT 0,
                overwrite_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                manual_protected_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dv_change_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_point_id INTEGER NOT NULL,
                old_value REAL,
                new_value REAL,
                operation_type TEXT NOT NULL,
                source_batch_id INTEGER,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                note TEXT,
                FOREIGN KEY (data_point_id) REFERENCES dv_data_points(id)
            );

            CREATE TABLE IF NOT EXISTS dv_integration_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_names TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                point_count INTEGER NOT NULL DEFAULT 0,
                apparent_demand_count INTEGER NOT NULL DEFAULT 0,
                validation_summary TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dv_integrated_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER,
                week_start TEXT NOT NULL,
                week_end TEXT,
                business_year INTEGER,
                business_week INTEGER,
                week_label TEXT,
                display_date TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                source_country TEXT NOT NULL,
                product TEXT NOT NULL,
                category TEXT NOT NULL,
                mainstream_status TEXT NOT NULL,
                value REAL,
                unit TEXT NOT NULL DEFAULT '万吨',
                source_file TEXT,
                source_sheet TEXT,
                source_section TEXT,
                is_calculable INTEGER NOT NULL DEFAULT 0,
                validation_status TEXT,
                note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES dv_integration_batches(id)
            );

            CREATE TABLE IF NOT EXISTS order_finance_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_key TEXT NOT NULL UNIQUE,
                subsidiary TEXT NOT NULL,
                source_file TEXT,
                source_sheet TEXT,
                source_row_start INTEGER,
                source_row_end INTEGER,
                source_snapshot_date TEXT,
                product_name TEXT,
                purchase_contract_no TEXT,
                system_contract_no TEXT,
                buyer TEXT,
                seller TEXT,
                overseas_entity TEXT,
                terminal_customer TEXT,
                contract_date TEXT,
                trade_term TEXT,
                origin_port TEXT,
                destination_port TEXT,
                contract_quantity_mt REAL,
                contract_currency TEXT,
                contract_amount REAL,
                finance_bank TEXT,
                finance_amount_expected REAL,
                finance_amount_actual REAL,
                repaid_amount REAL,
                remaining_credit_amount REAL,
                finance_drawdown_date TEXT,
                finance_due_date TEXT,
                finance_days INTEGER,
                finance_status TEXT,
                latest_shipment_date TEXT,
                lc_latest_shipment_date TEXT,
                vessel_voyage TEXT,
                bill_of_lading_date TEXT,
                bill_of_lading_no TEXT,
                document_submission_date TEXT,
                collection_date TEXT,
                actual_shipped_quantity_mt REAL,
                actual_goods_amount REAL,
                tail_amount REAL,
                tail_payment_date TEXT,
                executor TEXT,
                business_status TEXT,
                risk_level TEXT,
                planned_drawdown_date TEXT,
                planned_finance_amount REAL,
                amount_adjustment_note TEXT,
                repayment_requirement TEXT,
                repayment_requirement_status TEXT,
                next_action TEXT,
                next_follow_up_date TEXT,
                remark TEXT,
                manager_note TEXT,
                manual_override_fields TEXT,
                is_archived INTEGER NOT NULL DEFAULT 0,
                sales_contracts_json TEXT,
                settlement_json TEXT,
                management_plan_json TEXT,
                manual_change_log_json TEXT,
                corrections_json TEXT,
                import_warnings_json TEXT,
                source_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_order_finance_subsidiary
            ON order_finance_progress(subsidiary);

            CREATE INDEX IF NOT EXISTS idx_order_finance_due
            ON order_finance_progress(finance_due_date);

            CREATE INDEX IF NOT EXISTS idx_order_finance_status
            ON order_finance_progress(business_status);


            CREATE TABLE IF NOT EXISTS daily_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                info_type TEXT NOT NULL,
                contract_code TEXT NOT NULL,
                calc_date TEXT NOT NULL,
                close_price REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(info_type, contract_code, calc_date)
            );

            CREATE TABLE IF NOT EXISTS trading_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS strategy_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL UNIQUE,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS strategy_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                variety TEXT NOT NULL,
                variety_name TEXT,
                direction TEXT NOT NULL,
                open_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                multiplier INTEGER DEFAULT 100,
                contract TEXT DEFAULT '',
                current_price REAL,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES strategy_groups(id)
            );

            CREATE TABLE IF NOT EXISTS sh_junneng_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_month TEXT NOT NULL,
                direction TEXT NOT NULL,
                open_price REAL NOT NULL,
                open_volume REAL,
                current_price REAL,
                trade_quantity REAL,
                hold_quantity REAL,
                open_fee REAL DEFAULT 0,
                open_date TEXT,
                close_price REAL,
                close_volume REAL,
                close_fee REAL DEFAULT 0,
                close_date TEXT,
                profit REAL,
                status TEXT NOT NULL DEFAULT '未平仓',
                is_closed INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sh_junneng_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_trade_id INTEGER UNIQUE,
                contract_month TEXT NOT NULL,
                direction TEXT NOT NULL,
                open_price REAL NOT NULL,
                open_quantity REAL NOT NULL,
                remaining_quantity REAL NOT NULL,
                open_amount REAL,
                open_fee REAL NOT NULL DEFAULT 0,
                open_date TEXT NOT NULL,
                current_price REAL,
                business_code TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sh_junneng_close_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                close_date TEXT NOT NULL,
                close_quantity REAL NOT NULL,
                close_price REAL NOT NULL,
                close_amount REAL,
                close_fee REAL NOT NULL DEFAULT 0,
                open_fee_allocated REAL NOT NULL DEFAULT 0,
                close_sequence INTEGER NOT NULL DEFAULT 1,
                business_code TEXT,
                realized_profit REAL,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (position_id) REFERENCES sh_junneng_positions(id)
            );
            """
            )
        migrate_cache_schema(conn)
        migrate_auth_schema(conn)
        migrate_alert_schema(conn)
        migrate_mid_event_schema(conn)
        migrate_sh_junneng_schema(conn)
        migrate_dv_integration_schema(conn)

        ensure_admin_user(cur, "管理员")
        ensure_admin_user(cur, "admin")
        conn.commit()


def ensure_admin_user(cur, name: str) -> int:
    _exec(cur, "SELECT id FROM users WHERE name = ?", (name,))
    admin = cur.fetchone()
    if admin:
        admin_id = admin["id"]
    elif _is_pg():
        cur.execute(
            "INSERT INTO users (name, department, password_hash, role) VALUES (%s, %s, %s, %s) RETURNING id",
            (name, "管理部门", password_hash("admin"), "管理员"),
        )
        admin_id = cur.fetchone()["id"]
    else:
        cur.execute(
            "INSERT INTO users (name, department, password_hash, role) VALUES (?, ?, ?, ?)",
            (name, "管理部门", password_hash("admin"), "管理员"),
        )
        admin_id = cur.lastrowid
    for _, module_code, _ in MODULES:
        _exec(
            cur,
            "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit) VALUES (?, ?, 1, 1)",
            (admin_id, module_code),
        )
    return admin_id


def migrate_cache_schema(conn) -> None:
    """Create indexes. SQLite-specific migrations only apply to SQLite fallback."""
    cur = conn.cursor()
    if _is_pg():
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lookup
            ON calculated_data(info_type, year, month, calc_date);

            CREATE INDEX IF NOT EXISTS idx_prices
            ON daily_prices(info_type, contract_code, calc_date);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_prices_unique
            ON daily_prices(info_type, contract_code, calc_date);
            """
        )
        conn.commit()
        return
    # SQLite fallback
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(daily_prices)").fetchall()
    }
    if "trade_date" in columns:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_prices_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                info_type TEXT NOT NULL,
                contract_code TEXT NOT NULL,
                calc_date TEXT NOT NULL,
                close_price REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(info_type, contract_code, calc_date)
            );
            """
        )
        source_date = "calc_date" if "calc_date" in columns else "trade_date"
        conn.execute(
            f"""
            INSERT OR REPLACE INTO daily_prices_v2
                (info_type, contract_code, calc_date, close_price, created_at)
            SELECT info_type, contract_code, {source_date}, close_price, created_at
            FROM daily_prices
            WHERE {source_date} IS NOT NULL
            """
        )
        conn.execute("DROP TABLE daily_prices")
        conn.execute("ALTER TABLE daily_prices_v2 RENAME TO daily_prices")

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_lookup
        ON calculated_data(info_type, year, month, calc_date);

        CREATE INDEX IF NOT EXISTS idx_prices
        ON daily_prices(info_type, contract_code, calc_date);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_prices_unique
        ON daily_prices(info_type, contract_code, calc_date);
        """
    )


def migrate_alert_schema(conn) -> None:
    """Only for SQLite compatibility — PG schema already has reminder_users."""
    if _is_pg():
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(alert_settings)").fetchall()
    }
    if "reminder_users" not in columns:
        conn.execute("ALTER TABLE alert_settings ADD COLUMN reminder_users TEXT DEFAULT ''")


def migrate_mid_event_schema(conn) -> None:
    """Keep old production mid-event tables compatible with current code."""
    if _is_pg():
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'strategy_groups'
            """
        )
        columns = {row["column_name"] for row in cur.fetchall()}
        if "created_by" not in columns:
            cur.execute("ALTER TABLE strategy_groups ADD COLUMN created_by TEXT")
        if "updated_by" not in columns:
            cur.execute("ALTER TABLE strategy_groups ADD COLUMN updated_by TEXT")
        if "creator" in columns:
            cur.execute(
                """
                UPDATE strategy_groups
                SET created_by = creator
                WHERE created_by IS NULL AND creator IS NOT NULL
                """
            )
        conn.commit()
        return

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(strategy_groups)").fetchall()
    }
    if "created_by" not in columns:
        conn.execute("ALTER TABLE strategy_groups ADD COLUMN created_by TEXT")
    if "updated_by" not in columns:
        conn.execute("ALTER TABLE strategy_groups ADD COLUMN updated_by TEXT")
    if "creator" in columns:
        conn.execute(
            """
            UPDATE strategy_groups
            SET created_by = creator
            WHERE created_by IS NULL AND creator IS NOT NULL
            """
        )


def migrate_auth_schema(conn) -> None:
    if _is_pg():
        cur = conn.cursor()
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_guest INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cannot_change_password INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
        conn.commit()
        return
    user_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "is_guest" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_guest INTEGER NOT NULL DEFAULT 0")
    if "cannot_change_password" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN cannot_change_password INTEGER NOT NULL DEFAULT 0")
    session_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(user_sessions)").fetchall()
    }
    if "expires_at" not in session_columns:
        conn.execute("ALTER TABLE user_sessions ADD COLUMN expires_at TEXT")


def migrate_sh_junneng_schema(conn) -> None:
    legacy_columns = {
        "open_volume": "DOUBLE PRECISION",
        "current_price": "DOUBLE PRECISION",
        "trade_quantity": "DOUBLE PRECISION",
        "hold_quantity": "DOUBLE PRECISION",
        "open_fee": "DOUBLE PRECISION DEFAULT 0",
        "close_volume": "DOUBLE PRECISION",
        "close_fee": "DOUBLE PRECISION DEFAULT 0",
        "profit": "DOUBLE PRECISION",
    }
    if _is_pg():
        cur = conn.cursor()
        for name, col_type in legacy_columns.items():
            cur.execute(f"ALTER TABLE sh_junneng_trades ADD COLUMN IF NOT EXISTS {name} {col_type}")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sh_junneng_positions (
                id SERIAL PRIMARY KEY,
                source_trade_id INTEGER UNIQUE,
                contract_month TEXT NOT NULL,
                direction TEXT NOT NULL,
                open_price DOUBLE PRECISION NOT NULL,
                open_quantity DOUBLE PRECISION NOT NULL,
                remaining_quantity DOUBLE PRECISION NOT NULL,
                open_amount DOUBLE PRECISION,
                open_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
                open_date TEXT NOT NULL,
                current_price DOUBLE PRECISION,
                business_code TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sh_junneng_close_trades (
                id SERIAL PRIMARY KEY,
                position_id INTEGER NOT NULL,
                close_date TEXT NOT NULL,
                close_quantity DOUBLE PRECISION NOT NULL,
                close_price DOUBLE PRECISION NOT NULL,
                close_amount DOUBLE PRECISION,
                close_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
                open_fee_allocated DOUBLE PRECISION NOT NULL DEFAULT 0,
                close_sequence INTEGER NOT NULL DEFAULT 1,
                business_code TEXT,
                realized_profit DOUBLE PRECISION,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (position_id) REFERENCES sh_junneng_positions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_contract
            ON sh_junneng_trades(contract_month);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_open_date
            ON sh_junneng_trades(open_date);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_date
            ON sh_junneng_trades(close_date);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_status
            ON sh_junneng_trades(status);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_positions_contract
            ON sh_junneng_positions(contract_month);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_positions_open_date
            ON sh_junneng_positions(open_date);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_position
            ON sh_junneng_close_trades(position_id);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_date
            ON sh_junneng_close_trades(close_date);
            """
        )
        backfill_sh_junneng_positions(conn)
        conn.commit()
        return
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(sh_junneng_trades)").fetchall()
    }
    sqlite_types = {
        "open_volume": "REAL",
        "current_price": "REAL",
        "trade_quantity": "REAL",
        "hold_quantity": "REAL",
        "open_fee": "REAL DEFAULT 0",
        "close_volume": "REAL",
        "close_fee": "REAL DEFAULT 0",
        "profit": "REAL",
    }
    for name, col_type in sqlite_types.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE sh_junneng_trades ADD COLUMN {name} {col_type}")
    # SQLite: use executescript for multi-statement
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sh_junneng_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_trade_id INTEGER UNIQUE,
            contract_month TEXT NOT NULL,
            direction TEXT NOT NULL,
            open_price REAL NOT NULL,
            open_quantity REAL NOT NULL,
            remaining_quantity REAL NOT NULL,
            open_amount REAL,
            open_fee REAL NOT NULL DEFAULT 0,
            open_date TEXT NOT NULL,
            current_price REAL,
            business_code TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_by TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sh_junneng_close_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            close_date TEXT NOT NULL,
            close_quantity REAL NOT NULL,
            close_price REAL NOT NULL,
            close_amount REAL,
            close_fee REAL NOT NULL DEFAULT 0,
            open_fee_allocated REAL NOT NULL DEFAULT 0,
            close_sequence INTEGER NOT NULL DEFAULT 1,
            business_code TEXT,
            realized_profit REAL,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_by TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (position_id) REFERENCES sh_junneng_positions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_contract
        ON sh_junneng_trades(contract_month);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_open_date
        ON sh_junneng_trades(open_date);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_date
        ON sh_junneng_trades(close_date);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_status
        ON sh_junneng_trades(status);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_positions_contract
        ON sh_junneng_positions(contract_month);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_positions_open_date
        ON sh_junneng_positions(open_date);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_position
        ON sh_junneng_close_trades(position_id);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_date
        ON sh_junneng_close_trades(close_date);
        """
    )
    backfill_sh_junneng_positions(conn)


def backfill_sh_junneng_positions(conn) -> None:
    cur = conn.cursor()
    existing = _exec(cur, "SELECT COUNT(*) AS count FROM sh_junneng_positions").fetchone()
    if existing and existing["count"]:
        return
    rows = _exec(cur, "SELECT * FROM sh_junneng_trades ORDER BY id").fetchall()
    for row in rows:
        item = dict(row)
        quantity = item.get("trade_quantity") or item.get("open_volume") or item.get("close_volume") or 0
        if not quantity:
            continue
        is_closed = item.get("is_closed") in {1, "1", True, "已平仓"} or item.get("status") == "已结算"
        close_quantity = item.get("close_volume") or quantity
        remaining = 0 if is_closed else (item.get("hold_quantity") if item.get("hold_quantity") is not None else quantity)
        business_code = f"SHJN-{item.get('id')}"
        _exec(
            cur,
            """
            INSERT INTO sh_junneng_positions
                (source_trade_id, contract_month, direction, open_price, open_quantity,
                 remaining_quantity, open_amount, open_fee, open_date, current_price,
                 business_code, status, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("id"),
                item.get("contract_month"),
                item.get("direction"),
                item.get("open_price"),
                quantity,
                remaining,
                (item.get("open_price") or 0) * quantity,
                item.get("open_fee") or 0,
                item.get("open_date") or item.get("created_at", "")[:10],
                item.get("current_price"),
                business_code,
                "closed" if is_closed else "open",
                item.get("created_by"),
                item.get("updated_by"),
            ),
        )
        position_id = last_insert_id(conn)
        if is_closed and item.get("close_date") and item.get("close_price"):
            _exec(
                cur,
                """
                INSERT INTO sh_junneng_close_trades
                    (position_id, close_date, close_quantity, close_price, close_amount,
                     close_fee, open_fee_allocated, close_sequence, business_code,
                     realized_profit, created_by, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    item.get("close_date"),
                    close_quantity,
                    item.get("close_price"),
                    (item.get("close_price") or 0) * close_quantity,
                    item.get("close_fee") or 0,
                    item.get("open_fee") or 0,
                    1,
                    business_code,
                    item.get("profit"),
                    item.get("created_by"),
                    item.get("updated_by"),
                ),
            )


def migrate_dv_integration_schema(conn) -> None:
    columns = {
        "week_end": "TEXT",
        "business_year": "INTEGER",
        "business_week": "INTEGER",
        "week_label": "TEXT",
    }
    if _is_pg():
        cur = conn.cursor()
        for name, col_type in columns.items():
            cur.execute(f"ALTER TABLE dv_integrated_points ADD COLUMN IF NOT EXISTS {name} {col_type}")
        conn.commit()
        return
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dv_integrated_points)").fetchall()
    }
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE dv_integrated_points ADD COLUMN {name} {col_type}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def create_session(user_id: int, ttl_hours: int = 24 * 7) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (_utc_now() + timedelta(hours=ttl_hours)).isoformat()
    with connect() as conn:
        cur = conn.cursor()
        _exec(cur,
            "INSERT INTO user_sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at),
        )
        conn.commit()
    return token


def get_user_by_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    with connect() as conn:
        cur = conn.cursor()
        _exec(cur,
            """
            SELECT u.*, s.expires_at
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.status = '活跃' AND u.status = '启用'
            """,
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        expires_at = _parse_dt(row["expires_at"])
        if expires_at and expires_at <= _utc_now():
            _exec(cur, "UPDATE user_sessions SET status = '已过期' WHERE token = ?", (token,))
            return None
        _exec(cur, "UPDATE user_sessions SET last_activity = CURRENT_TIMESTAMP WHERE token = ?", (token,))
        return dict(row)


def ensure_guest_user() -> dict:
    with connect() as conn:
        cur = conn.cursor()
        row = _exec(cur, "SELECT * FROM users WHERE name = ?", ("guest",)).fetchone()
        if row:
            guest_id = row["id"]
            _exec(
                cur,
                """
                UPDATE users
                SET role = 'guest', status = '启用', is_guest = 1, cannot_change_password = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (guest_id,),
            )
        elif _is_pg():
            cur.execute(
                """
                INSERT INTO users (name, department, password_hash, role, status, is_guest, cannot_change_password)
                VALUES (%s, %s, %s, %s, %s, 1, 1)
                RETURNING id
                """,
                ("guest", "访客", password_hash(secrets.token_urlsafe(16)), "guest", "启用"),
            )
            guest_id = cur.fetchone()["id"]
        else:
            cur.execute(
                """
                INSERT INTO users (name, department, password_hash, role, status, is_guest, cannot_change_password)
                VALUES (?, ?, ?, ?, ?, 1, 1)
                """,
                ("guest", "访客", password_hash(secrets.token_urlsafe(16)), "guest", "启用"),
            )
            guest_id = cur.lastrowid
        _exec(cur, "DELETE FROM module_permissions WHERE user_id = ?", (guest_id,))
        for module_code, can_view in {
            "info_summary": 1,
            "data_visualization_chart": 1,
        }.items():
            _exec(
                cur,
                "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit) VALUES (?, ?, ?, 0)",
                (guest_id, module_code, can_view),
            )
        _exec(cur, "SELECT * FROM users WHERE id = ?", (guest_id,))
        guest = cur.fetchone()
    return dict(guest)


def log_operation(
    user_id: Optional[int],
    module_code: str,
    operation_type: str,
    description: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> None:
    with connect() as conn:
        cur = conn.cursor()
        _exec(cur,
            """
            INSERT INTO operation_logs
                (user_id, module_code, entity_type, entity_id, operation_type, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, module_code, entity_type, entity_id, operation_type, description),
        )
        conn.commit()
