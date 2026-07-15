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

TRADING_MANAGEMENT_TABLES = (
    "trading_accounts",
    "trading_import_batches",
    "trading_source_rows",
    "trading_fact_identities",
    "trading_trade_facts",
    "trading_close_facts",
    "trading_position_snapshots",
    "trading_contract_specs",
    "trading_fact_close_allocations",
    "trading_close_trade_links",
    "trading_business_subjects",
    "trading_strategies",
    "trading_business_assignments",
    "trading_business_close_allocations",
    "trading_business_allocation_audit",
)

MODULES = [
    ("台账管理", "sh_junneng", "上海钧能台账"),
    ("台账管理", "steel_export", "钢材出口套保台账"),
    ("台账管理", "subsidiary_hedging", "子公司套保台账"),
    ("台账管理", "option_trading", "期权交易台账"),
    ("交易管理", "trading_overview", "总览"),
    ("交易管理", "trading_positions", "持仓与交易"),
    ("交易管理", "trading_sh_junneng", "上海钧能台账"),
    ("交易管理", "trading_options", "期权台账"),
    ("交易管理", "trading_export", "汇总与导出"),
    ("信息预警管理", "info_summary", "实时信息汇总"),
    ("信息预警管理", "risk_alert", "风险预警"),
    ("信息预警管理", "mid_event_monitor", "事中风险监控"),
    ("数据可视化管理", "data_visualization_integration", "数据整合"),
    ("数据可视化管理", "data_visualization_data", "数据管理"),
    ("数据可视化管理", "data_visualization_chart", "数据展示"),
    ("订单融资管理", "order_finance_progress", "订单融资进度"),
    ("订单融资管理", "order_finance_capital", "融资资金监控"),
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
    normalized = sql.strip()
    # INSERT OR REPLACE -> INSERT ... ON CONFLICT DO UPDATE
    m = re.match(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\((.+?)\)\s*VALUES\s*\((.+?)\)$",
        normalized, re.DOTALL | re.IGNORECASE,
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
        r"INSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)\b.*", normalized, re.DOTALL | re.IGNORECASE,
    )
    if m2:
        return re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", normalized, count=1, flags=re.IGNORECASE) + " ON CONFLICT DO NOTHING"
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
        psycopg2.extras.execute_batch(cur, sql, seq, page_size=1000)
        return
    cur.executemany(sql, seq)


def _secure_postgres_tables(cur, tables) -> None:
    for table in tables:
        cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    cur.execute(f"REVOKE ALL ON TABLE {', '.join(tables)} FROM anon, authenticated")
    sequences = ", ".join(f"{table}_id_seq" for table in tables)
    cur.execute(f"REVOKE ALL ON SEQUENCE {sequences} FROM anon, authenticated")


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
                name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                department TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '启用',
                password_change_recommended INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS module_permissions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                module_code TEXT NOT NULL,
                can_view INTEGER NOT NULL DEFAULT 1,
                can_edit INTEGER NOT NULL DEFAULT 0,
                can_sensitive INTEGER NOT NULL DEFAULT 0,
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

            CREATE INDEX IF NOT EXISTS idx_operation_logs_created_id
            ON operation_logs(created_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_operation_logs_user_created_id
            ON operation_logs(user_id, created_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_operation_logs_type_created_id
            ON operation_logs(operation_type, created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS operation_log_archives (
                id SERIAL PRIMARY KEY,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                object_path TEXT NOT NULL UNIQUE,
                row_count INTEGER NOT NULL,
                first_created_at TEXT NOT NULL,
                last_created_at TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                compressed_bytes INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                restored_at TEXT,
                UNIQUE(period_start, period_end)
            );

            CREATE TABLE IF NOT EXISTS operation_log_archive_users (
                id SERIAL PRIMARY KEY,
                archive_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                UNIQUE(archive_id, user_id),
                FOREIGN KEY (archive_id) REFERENCES operation_log_archives(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id)
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
                shipment_confirmed_date TEXT,
                shipment_confirmed_by TEXT,
                shipment_confirmed_at TEXT,
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
                name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                department TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '启用',
                password_change_recommended INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS module_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                module_code TEXT NOT NULL,
                can_view INTEGER NOT NULL DEFAULT 1,
                can_edit INTEGER NOT NULL DEFAULT 0,
                can_sensitive INTEGER NOT NULL DEFAULT 0,
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

            CREATE INDEX IF NOT EXISTS idx_operation_logs_created_id
            ON operation_logs(created_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_operation_logs_user_created_id
            ON operation_logs(user_id, created_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_operation_logs_type_created_id
            ON operation_logs(operation_type, created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS operation_log_archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                object_path TEXT NOT NULL UNIQUE,
                row_count INTEGER NOT NULL,
                first_created_at TEXT NOT NULL,
                last_created_at TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                compressed_bytes INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                restored_at TEXT,
                UNIQUE(period_start, period_end)
            );

            CREATE TABLE IF NOT EXISTS operation_log_archive_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                archive_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                UNIQUE(archive_id, user_id),
                FOREIGN KEY (archive_id) REFERENCES operation_log_archives(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id)
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
                shipment_confirmed_date TEXT,
                shipment_confirmed_by TEXT,
                shipment_confirmed_at TEXT,
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
        migrate_order_finance_schema(conn)
        migrate_dv_integration_schema(conn)
        migrate_iron_ore_basis_schema(conn)
        migrate_trading_management_schema(conn)

        ensure_admin_user(cur, "管理员")
        ensure_admin_user(cur, "admin")
        sync_trading_module_permissions(cur)
        conn.commit()


def migrate_iron_ore_basis_schema(conn) -> None:
    """Create isolated iron-ore basis result and audit-detail tables."""
    if _is_pg():
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS iron_ore_basis_results (
                id SERIAL PRIMARY KEY,
                business_key TEXT NOT NULL UNIQUE,
                business_date TEXT NOT NULL,
                business_week INTEGER NOT NULL,
                week_label TEXT NOT NULL,
                business_year INTEGER NOT NULL,
                port TEXT NOT NULL,
                product TEXT NOT NULL,
                wet_spot_price DOUBLE PRECISION NOT NULL,
                quality_adjustment DOUBLE PRECISION NOT NULL,
                brand_adjustment DOUBLE PRECISION NOT NULL,
                standardized_spot_price DOUBLE PRECISION NOT NULL,
                futures_series TEXT NOT NULL DEFAULT 'I0',
                futures_close DOUBLE PRECISION NOT NULL,
                basis DOUBLE PRECISION NOT NULL,
                data_status TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                parameter_version TEXT NOT NULL,
                source_workbook_name TEXT NOT NULL,
                source_workbook_sha256 TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(business_date, port, product, rule_version, parameter_version)
            );

            CREATE TABLE IF NOT EXISTS iron_ore_basis_details (
                id SERIAL PRIMARY KEY,
                result_id INTEGER NOT NULL UNIQUE REFERENCES iron_ore_basis_results(id) ON DELETE CASCADE,
                business_key TEXT NOT NULL UNIQUE,
                business_date TEXT NOT NULL,
                week_label TEXT NOT NULL,
                business_year INTEGER NOT NULL,
                port TEXT NOT NULL,
                product TEXT NOT NULL,
                ebc_indicator_code TEXT,
                ebc_indicator_name TEXT,
                ebc_price_fe DOUBLE PRECISION,
                wet_spot_price DOUBLE PRECISION NOT NULL,
                parameter_year INTEGER NOT NULL,
                parameter_type TEXT NOT NULL,
                fe DOUBLE PRECISION NOT NULL,
                sio2 DOUBLE PRECISION NOT NULL,
                al2o3 DOUBLE PRECISION NOT NULL,
                phosphorus DOUBLE PRECISION NOT NULL,
                sulfur DOUBLE PRECISION NOT NULL,
                h2o DOUBLE PRECISION NOT NULL,
                sulfur_defaulted INTEGER NOT NULL DEFAULT 0,
                price_proxy_indicator TEXT,
                price_parameter_spec_diff INTEGER NOT NULL DEFAULT 0,
                fe_adjustment_x DOUBLE PRECISION NOT NULL,
                brand_adjustment DOUBLE PRECISION NOT NULL,
                futures_series TEXT NOT NULL,
                futures_close DOUBLE PRECISION NOT NULL,
                fe_adjustment DOUBLE PRECISION NOT NULL,
                sio2_adjustment DOUBLE PRECISION NOT NULL,
                al2o3_adjustment DOUBLE PRECISION NOT NULL,
                phosphorus_adjustment DOUBLE PRECISION NOT NULL,
                sulfur_adjustment DOUBLE PRECISION NOT NULL,
                quality_adjustment DOUBLE PRECISION NOT NULL,
                dry_spot_price DOUBLE PRECISION NOT NULL,
                standardized_spot_price DOUBLE PRECISION NOT NULL,
                basis DOUBLE PRECISION NOT NULL,
                data_status TEXT NOT NULL,
                note TEXT,
                rule_version TEXT NOT NULL,
                parameter_source TEXT NOT NULL,
                parameter_version TEXT NOT NULL,
                ebc_original_port TEXT,
                source_workbook_name TEXT NOT NULL,
                source_workbook_sha256 TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS iron_ore_basis_sync_runs (
                id SERIAL PRIMARY KEY,
                slot_key TEXT NOT NULL UNIQUE,
                trigger_type TEXT NOT NULL,
                target_start_date TEXT NOT NULL,
                target_end_date TEXT NOT NULL,
                status TEXT NOT NULL,
                source_points_seen INTEGER NOT NULL DEFAULT 0,
                source_points_inserted INTEGER NOT NULL DEFAULT 0,
                source_differences INTEGER NOT NULL DEFAULT 0,
                combinations_written INTEGER NOT NULL DEFAULT 0,
                combinations_skipped INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_summary TEXT,
                started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS iron_ore_basis_source_points (
                id SERIAL PRIMARY KEY,
                source_name TEXT NOT NULL,
                indicator_key TEXT NOT NULL,
                business_date TEXT NOT NULL,
                canonical_value DOUBLE PRECISION NOT NULL,
                canonical_payload_sha256 TEXT NOT NULL,
                first_run_id INTEGER REFERENCES iron_ore_basis_sync_runs(id) ON DELETE SET NULL,
                last_observed_value DOUBLE PRECISION NOT NULL,
                last_observed_payload_sha256 TEXT NOT NULL,
                difference_detected INTEGER NOT NULL DEFAULT 0,
                difference_count INTEGER NOT NULL DEFAULT 0,
                first_observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_name, indicator_key, business_date)
            );

            CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_results_query
            ON iron_ore_basis_results(business_year, port, product, business_date);
            CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_results_optimal
            ON iron_ore_basis_results(business_year, data_status, business_date, basis);
            CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_details_result
            ON iron_ore_basis_details(result_id);
            CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_sync_runs_window
            ON iron_ore_basis_sync_runs(target_start_date, target_end_date, status);
            CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_source_points_date
            ON iron_ore_basis_source_points(business_date, source_name, indicator_key);

            ALTER TABLE iron_ore_basis_results ENABLE ROW LEVEL SECURITY;
            ALTER TABLE iron_ore_basis_details ENABLE ROW LEVEL SECURITY;
            ALTER TABLE iron_ore_basis_sync_runs ENABLE ROW LEVEL SECURITY;
            ALTER TABLE iron_ore_basis_source_points ENABLE ROW LEVEL SECURITY;
            REVOKE ALL ON TABLE iron_ore_basis_results, iron_ore_basis_details,
                iron_ore_basis_sync_runs, iron_ore_basis_source_points FROM anon, authenticated;
            REVOKE ALL ON SEQUENCE iron_ore_basis_results_id_seq, iron_ore_basis_details_id_seq,
                iron_ore_basis_sync_runs_id_seq, iron_ore_basis_source_points_id_seq FROM anon, authenticated;
            """
        )
        return

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS iron_ore_basis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_key TEXT NOT NULL UNIQUE,
            business_date TEXT NOT NULL,
            business_week INTEGER NOT NULL,
            week_label TEXT NOT NULL,
            business_year INTEGER NOT NULL,
            port TEXT NOT NULL,
            product TEXT NOT NULL,
            wet_spot_price REAL NOT NULL,
            quality_adjustment REAL NOT NULL,
            brand_adjustment REAL NOT NULL,
            standardized_spot_price REAL NOT NULL,
            futures_series TEXT NOT NULL DEFAULT 'I0',
            futures_close REAL NOT NULL,
            basis REAL NOT NULL,
            data_status TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            parameter_version TEXT NOT NULL,
            source_workbook_name TEXT NOT NULL,
            source_workbook_sha256 TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_date, port, product, rule_version, parameter_version)
        );

        CREATE TABLE IF NOT EXISTS iron_ore_basis_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL UNIQUE,
            business_key TEXT NOT NULL UNIQUE,
            business_date TEXT NOT NULL,
            week_label TEXT NOT NULL,
            business_year INTEGER NOT NULL,
            port TEXT NOT NULL,
            product TEXT NOT NULL,
            ebc_indicator_code TEXT,
            ebc_indicator_name TEXT,
            ebc_price_fe REAL,
            wet_spot_price REAL NOT NULL,
            parameter_year INTEGER NOT NULL,
            parameter_type TEXT NOT NULL,
            fe REAL NOT NULL,
            sio2 REAL NOT NULL,
            al2o3 REAL NOT NULL,
            phosphorus REAL NOT NULL,
            sulfur REAL NOT NULL,
            h2o REAL NOT NULL,
            sulfur_defaulted INTEGER NOT NULL DEFAULT 0,
            price_proxy_indicator TEXT,
            price_parameter_spec_diff INTEGER NOT NULL DEFAULT 0,
            fe_adjustment_x REAL NOT NULL,
            brand_adjustment REAL NOT NULL,
            futures_series TEXT NOT NULL,
            futures_close REAL NOT NULL,
            fe_adjustment REAL NOT NULL,
            sio2_adjustment REAL NOT NULL,
            al2o3_adjustment REAL NOT NULL,
            phosphorus_adjustment REAL NOT NULL,
            sulfur_adjustment REAL NOT NULL,
            quality_adjustment REAL NOT NULL,
            dry_spot_price REAL NOT NULL,
            standardized_spot_price REAL NOT NULL,
            basis REAL NOT NULL,
            data_status TEXT NOT NULL,
            note TEXT,
            rule_version TEXT NOT NULL,
            parameter_source TEXT NOT NULL,
            parameter_version TEXT NOT NULL,
            ebc_original_port TEXT,
            source_workbook_name TEXT NOT NULL,
            source_workbook_sha256 TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (result_id) REFERENCES iron_ore_basis_results(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS iron_ore_basis_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_key TEXT NOT NULL UNIQUE,
            trigger_type TEXT NOT NULL,
            target_start_date TEXT NOT NULL,
            target_end_date TEXT NOT NULL,
            status TEXT NOT NULL,
            source_points_seen INTEGER NOT NULL DEFAULT 0,
            source_points_inserted INTEGER NOT NULL DEFAULT 0,
            source_differences INTEGER NOT NULL DEFAULT 0,
            combinations_written INTEGER NOT NULL DEFAULT 0,
            combinations_skipped INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_summary TEXT,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS iron_ore_basis_source_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            indicator_key TEXT NOT NULL,
            business_date TEXT NOT NULL,
            canonical_value REAL NOT NULL,
            canonical_payload_sha256 TEXT NOT NULL,
            first_run_id INTEGER,
            last_observed_value REAL NOT NULL,
            last_observed_payload_sha256 TEXT NOT NULL,
            difference_detected INTEGER NOT NULL DEFAULT 0,
            difference_count INTEGER NOT NULL DEFAULT 0,
            first_observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_name, indicator_key, business_date),
            FOREIGN KEY (first_run_id) REFERENCES iron_ore_basis_sync_runs(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_results_query
        ON iron_ore_basis_results(business_year, port, product, business_date);
        CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_results_optimal
        ON iron_ore_basis_results(business_year, data_status, business_date, basis);
        CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_details_result
        ON iron_ore_basis_details(result_id);
        CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_sync_runs_window
        ON iron_ore_basis_sync_runs(target_start_date, target_end_date, status);
        CREATE INDEX IF NOT EXISTS idx_iron_ore_basis_source_points_date
        ON iron_ore_basis_source_points(business_date, source_name, indicator_key);
        """
    )


def migrate_trading_management_schema(conn) -> None:
    """Create isolated trading-management tables without touching legacy ledgers."""
    cur = conn.cursor()
    if _is_pg():
        cur.execute("CREATE SCHEMA IF NOT EXISTS codex_backups")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS codex_backups.module_permissions_before_trading_management_20260712
            AS TABLE public.module_permissions WITH DATA
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_accounts (
                id SERIAL PRIMARY KEY,
                account_code TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                masked_name TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS trading_import_batches (
                id SERIAL PRIMARY KEY,
                account_id INTEGER NOT NULL,
                range_start TEXT,
                range_end TEXT,
                position_snapshot_date TEXT,
                status TEXT NOT NULL DEFAULT 'preview',
                trade_file_name TEXT, close_file_name TEXT, position_file_name TEXT,
                trade_file_sha256 TEXT, close_file_sha256 TEXT, position_file_sha256 TEXT,
                trade_count INTEGER NOT NULL DEFAULT 0,
                close_count INTEGER NOT NULL DEFAULT 0,
                position_count INTEGER NOT NULL DEFAULT 0,
                parse_summary TEXT,
                supersedes_batch_id INTEGER,
                created_by TEXT, confirmed_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                confirmed_at TEXT,
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id),
                FOREIGN KEY (supersedes_batch_id) REFERENCES trading_import_batches(id)
            );
            CREATE TABLE IF NOT EXISTS trading_source_rows (
                id SERIAL PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_file TEXT NOT NULL,
                source_sheet TEXT NOT NULL,
                source_row_no INTEGER NOT NULL,
                raw_hash TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id)
            );
            CREATE TABLE IF NOT EXISTS trading_fact_identities (
                id SERIAL PRIMARY KEY,
                account_id INTEGER NOT NULL,
                fact_type TEXT NOT NULL,
                stable_key TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, fact_type, stable_key),
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id)
            );
            CREATE TABLE IF NOT EXISTS trading_trade_facts (
                id SERIAL PRIMARY KEY,
                identity_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                source_row_id INTEGER NOT NULL,
                trade_date TEXT NOT NULL, trade_time TEXT,
                exchange TEXT, contract TEXT NOT NULL, asset_type TEXT NOT NULL,
                side TEXT NOT NULL, open_close_raw TEXT, open_close TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL, price DOUBLE PRECISION NOT NULL,
                turnover DOUBLE PRECISION, fee DOUBLE PRECISION,
                hedge_flag TEXT, premium_cashflow DOUBLE PRECISION,
                data_status TEXT NOT NULL DEFAULT 'file_imported',
                verification_status TEXT NOT NULL DEFAULT 'pending_verification',
                FOREIGN KEY (identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id),
                FOREIGN KEY (source_row_id) REFERENCES trading_source_rows(id)
            );
            CREATE TABLE IF NOT EXISTS trading_close_facts (
                id SERIAL PRIMARY KEY,
                identity_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                source_row_id INTEGER NOT NULL,
                open_date TEXT, close_date TEXT NOT NULL,
                exchange TEXT, contract TEXT NOT NULL, asset_type TEXT NOT NULL,
                open_side TEXT NOT NULL, close_side TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                open_price DOUBLE PRECISION NOT NULL, close_price DOUBLE PRECISION NOT NULL,
                fact_close_pnl DOUBLE PRECISION NOT NULL,
                matched_fee DOUBLE PRECISION,
                fee_status TEXT NOT NULL DEFAULT 'pending_match',
                data_status TEXT NOT NULL DEFAULT 'file_imported',
                verification_status TEXT NOT NULL DEFAULT 'pending_verification',
                FOREIGN KEY (identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id),
                FOREIGN KEY (source_row_id) REFERENCES trading_source_rows(id)
            );
            CREATE TABLE IF NOT EXISTS trading_position_snapshots (
                id SERIAL PRIMARY KEY,
                identity_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                source_row_id INTEGER NOT NULL,
                snapshot_date TEXT NOT NULL, snapshot_time TEXT,
                exchange TEXT, contract TEXT NOT NULL, asset_type TEXT NOT NULL,
                direction TEXT NOT NULL, open_date TEXT,
                quantity DOUBLE PRECISION NOT NULL, average_price DOUBLE PRECISION NOT NULL,
                margin DOUBLE PRECISION,
                valuation_price DOUBLE PRECISION, floating_pnl DOUBLE PRECISION,
                market_time TEXT, valuation_status TEXT NOT NULL DEFAULT 'pending_calculation',
                data_status TEXT NOT NULL DEFAULT 'file_imported',
                verification_status TEXT NOT NULL DEFAULT 'pending_verification',
                FOREIGN KEY (identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id),
                FOREIGN KEY (source_row_id) REFERENCES trading_source_rows(id)
            );
            CREATE TABLE IF NOT EXISTS trading_contract_specs (
                id SERIAL PRIMARY KEY,
                exchange TEXT NOT NULL, product_code TEXT NOT NULL, asset_type TEXT NOT NULL,
                contract_multiplier DOUBLE PRECISION NOT NULL,
                price_tick DOUBLE PRECISION,
                source TEXT NOT NULL DEFAULT 'confirmed_config',
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, product_code, asset_type)
            );
            CREATE TABLE IF NOT EXISTS trading_fact_close_allocations (
                id SERIAL PRIMARY KEY,
                close_identity_id INTEGER NOT NULL,
                open_trade_identity_id INTEGER NOT NULL,
                matched_quantity DOUBLE PRECISION NOT NULL,
                match_rule_version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'matched',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (open_trade_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE TABLE IF NOT EXISTS trading_close_trade_links (
                id SERIAL PRIMARY KEY,
                close_identity_id INTEGER NOT NULL,
                close_trade_identity_id INTEGER NOT NULL,
                matched_quantity DOUBLE PRECISION NOT NULL,
                allocated_fee DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT 'matched',
                rule_version TEXT NOT NULL,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (close_trade_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_subjects (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT, updated_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS trading_strategies (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                merged_into_id INTEGER,
                source TEXT NOT NULL DEFAULT 'manual',
                created_by TEXT, updated_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (merged_into_id) REFERENCES trading_strategies(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_assignments (
                id SERIAL PRIMARY KEY,
                trade_identity_id INTEGER NOT NULL UNIQUE,
                business_subject_id INTEGER NOT NULL,
                business_type TEXT NOT NULL,
                strategy_id INTEGER,
                instruction_text TEXT,
                assigned_by TEXT, assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (trade_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (business_subject_id) REFERENCES trading_business_subjects(id),
                FOREIGN KEY (strategy_id) REFERENCES trading_strategies(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_close_allocations (
                id SERIAL PRIMARY KEY,
                close_identity_id INTEGER NOT NULL,
                open_trade_identity_id INTEGER NOT NULL,
                matched_quantity DOUBLE PRECISION NOT NULL,
                source TEXT NOT NULL DEFAULT 'fact_default',
                override_group_id TEXT,
                business_pnl DOUBLE PRECISION,
                rule_version TEXT NOT NULL,
                allocation_version INTEGER NOT NULL DEFAULT 1,
                created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (open_trade_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_allocation_audit (
                id SERIAL PRIMARY KEY,
                override_group_id TEXT NOT NULL,
                close_identity_id INTEGER NOT NULL,
                before_allocations TEXT NOT NULL,
                after_allocations TEXT NOT NULL,
                before_business_pnl DOUBLE PRECISION,
                after_business_pnl DOUBLE PRECISION,
                reason TEXT,
                operated_by TEXT NOT NULL,
                operated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_trading_batches_account_range ON trading_import_batches(account_id, range_start, range_end, status);
            CREATE INDEX IF NOT EXISTS idx_trading_trades_batch_date_contract ON trading_trade_facts(batch_id, trade_date, contract);
            CREATE INDEX IF NOT EXISTS idx_trading_closes_batch_date_contract ON trading_close_facts(batch_id, close_date, contract);
            CREATE INDEX IF NOT EXISTS idx_trading_positions_batch_date_contract ON trading_position_snapshots(batch_id, snapshot_date, contract);
            CREATE INDEX IF NOT EXISTS idx_trading_business_close ON trading_business_close_allocations(close_identity_id);
            CREATE INDEX IF NOT EXISTS idx_trading_business_open ON trading_business_close_allocations(open_trade_identity_id);
            """
        )
        _secure_postgres_tables(cur, TRADING_MANAGEMENT_TABLES)
    else:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trading_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_code TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                masked_name TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS trading_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL, range_start TEXT, range_end TEXT,
                position_snapshot_date TEXT, status TEXT NOT NULL DEFAULT 'preview',
                trade_file_name TEXT, close_file_name TEXT, position_file_name TEXT,
                trade_file_sha256 TEXT, close_file_sha256 TEXT, position_file_sha256 TEXT,
                trade_count INTEGER NOT NULL DEFAULT 0, close_count INTEGER NOT NULL DEFAULT 0,
                position_count INTEGER NOT NULL DEFAULT 0, parse_summary TEXT,
                supersedes_batch_id INTEGER, created_by TEXT, confirmed_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, confirmed_at TEXT,
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id),
                FOREIGN KEY (supersedes_batch_id) REFERENCES trading_import_batches(id)
            );
            CREATE TABLE IF NOT EXISTS trading_source_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL, source_type TEXT NOT NULL,
                source_file TEXT NOT NULL, source_sheet TEXT NOT NULL,
                source_row_no INTEGER NOT NULL, raw_hash TEXT NOT NULL, raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id)
            );
            CREATE TABLE IF NOT EXISTS trading_fact_identities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL, fact_type TEXT NOT NULL, stable_key TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, fact_type, stable_key),
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id)
            );
            CREATE TABLE IF NOT EXISTS trading_trade_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_id INTEGER NOT NULL, batch_id INTEGER NOT NULL, source_row_id INTEGER NOT NULL,
                trade_date TEXT NOT NULL, trade_time TEXT, exchange TEXT, contract TEXT NOT NULL,
                asset_type TEXT NOT NULL, side TEXT NOT NULL, open_close_raw TEXT,
                open_close TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
                turnover REAL, fee REAL, hedge_flag TEXT, premium_cashflow REAL,
                data_status TEXT NOT NULL DEFAULT 'file_imported',
                verification_status TEXT NOT NULL DEFAULT 'pending_verification',
                FOREIGN KEY (identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id),
                FOREIGN KEY (source_row_id) REFERENCES trading_source_rows(id)
            );
            CREATE TABLE IF NOT EXISTS trading_close_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_id INTEGER NOT NULL, batch_id INTEGER NOT NULL, source_row_id INTEGER NOT NULL,
                open_date TEXT, close_date TEXT NOT NULL, exchange TEXT, contract TEXT NOT NULL,
                asset_type TEXT NOT NULL, open_side TEXT NOT NULL, close_side TEXT NOT NULL,
                quantity REAL NOT NULL, open_price REAL NOT NULL, close_price REAL NOT NULL,
                fact_close_pnl REAL NOT NULL, matched_fee REAL,
                fee_status TEXT NOT NULL DEFAULT 'pending_match',
                data_status TEXT NOT NULL DEFAULT 'file_imported',
                verification_status TEXT NOT NULL DEFAULT 'pending_verification',
                FOREIGN KEY (identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id),
                FOREIGN KEY (source_row_id) REFERENCES trading_source_rows(id)
            );
            CREATE TABLE IF NOT EXISTS trading_position_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_id INTEGER NOT NULL, batch_id INTEGER NOT NULL, source_row_id INTEGER NOT NULL,
                snapshot_date TEXT NOT NULL, snapshot_time TEXT, exchange TEXT, contract TEXT NOT NULL,
                asset_type TEXT NOT NULL, direction TEXT NOT NULL, open_date TEXT,
                quantity REAL NOT NULL, average_price REAL NOT NULL, margin REAL,
                valuation_price REAL, floating_pnl REAL, market_time TEXT,
                valuation_status TEXT NOT NULL DEFAULT 'pending_calculation',
                data_status TEXT NOT NULL DEFAULT 'file_imported',
                verification_status TEXT NOT NULL DEFAULT 'pending_verification',
                FOREIGN KEY (identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (batch_id) REFERENCES trading_import_batches(id),
                FOREIGN KEY (source_row_id) REFERENCES trading_source_rows(id)
            );
            CREATE TABLE IF NOT EXISTS trading_contract_specs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL, product_code TEXT NOT NULL, asset_type TEXT NOT NULL,
                contract_multiplier REAL NOT NULL, price_tick REAL,
                source TEXT NOT NULL DEFAULT 'confirmed_config', is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, product_code, asset_type)
            );
            CREATE TABLE IF NOT EXISTS trading_fact_close_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                close_identity_id INTEGER NOT NULL, open_trade_identity_id INTEGER NOT NULL,
                matched_quantity REAL NOT NULL, match_rule_version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'matched', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (open_trade_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE TABLE IF NOT EXISTS trading_close_trade_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                close_identity_id INTEGER NOT NULL, close_trade_identity_id INTEGER NOT NULL,
                matched_quantity REAL NOT NULL, allocated_fee REAL,
                status TEXT NOT NULL DEFAULT 'matched', rule_version TEXT NOT NULL,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (close_trade_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1, created_by TEXT, updated_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS trading_strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1, merged_into_id INTEGER,
                source TEXT NOT NULL DEFAULT 'manual', created_by TEXT, updated_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (merged_into_id) REFERENCES trading_strategies(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_identity_id INTEGER NOT NULL UNIQUE, business_subject_id INTEGER NOT NULL,
                business_type TEXT NOT NULL, strategy_id INTEGER, instruction_text TEXT,
                assigned_by TEXT, assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (trade_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (business_subject_id) REFERENCES trading_business_subjects(id),
                FOREIGN KEY (strategy_id) REFERENCES trading_strategies(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_close_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                close_identity_id INTEGER NOT NULL, open_trade_identity_id INTEGER NOT NULL,
                matched_quantity REAL NOT NULL, source TEXT NOT NULL DEFAULT 'fact_default',
                override_group_id TEXT, business_pnl REAL, rule_version TEXT NOT NULL,
                allocation_version INTEGER NOT NULL DEFAULT 1,
                created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id),
                FOREIGN KEY (open_trade_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE TABLE IF NOT EXISTS trading_business_allocation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                override_group_id TEXT NOT NULL, close_identity_id INTEGER NOT NULL,
                before_allocations TEXT NOT NULL, after_allocations TEXT NOT NULL,
                before_business_pnl REAL, after_business_pnl REAL, reason TEXT,
                operated_by TEXT NOT NULL, operated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (close_identity_id) REFERENCES trading_fact_identities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_trading_batches_account_range ON trading_import_batches(account_id, range_start, range_end, status);
            CREATE INDEX IF NOT EXISTS idx_trading_trades_batch_date_contract ON trading_trade_facts(batch_id, trade_date, contract);
            CREATE INDEX IF NOT EXISTS idx_trading_closes_batch_date_contract ON trading_close_facts(batch_id, close_date, contract);
            CREATE INDEX IF NOT EXISTS idx_trading_positions_batch_date_contract ON trading_position_snapshots(batch_id, snapshot_date, contract);
            CREATE INDEX IF NOT EXISTS idx_trading_business_close ON trading_business_close_allocations(close_identity_id);
            CREATE INDEX IF NOT EXISTS idx_trading_business_open ON trading_business_close_allocations(open_trade_identity_id);
            """
        )
    reference_accounts = [("hongyuan_futures", "宏源期货账户", "宏源期货")]
    reference_subjects = [
        "东北组", "山东组", "天津组", "唐山组", "大客户组", "南方组", "黄骅组", "期货组",
        "采购组", "上海钧能", "山西建龙", "吉林恒联", "吉林建龙", "建龙北满", "双鸭山建龙",
        "抚顺新钢铁", "建龙西林", "建龙阿城", "承德建龙", "吕梁建龙",
    ]
    reference_strategies = [
        "一般套保-换月", "一般套保-锁固定价", "一般套保-套保", "一般套保-交割", "一般套保-汇率",
        "战略套保-自主建仓", "战略套保-跨期套利", "战略套保-跨品种套利", "战略套保-内外盘套利",
        "战略套保-期权结构化套利", "代内部公司套保", "代内部公司换月", "代内部公司锁汇", "代外部公司下单",
    ]
    reference_specs = [
        ("上期所", "rb", "future", 10, 1),
        ("上期所", "hc", "future", 10, 1),
        ("大商所", "i", "future", 100, 0.5),
        ("大商所", "i", "option", 100, 0.1),
        ("大商所", "j", "future", 100, 0.5),
    ]
    for account_code, display_name, masked_name in reference_accounts:
        if not _exec(cur, "SELECT id FROM trading_accounts WHERE account_code = ?", (account_code,)).fetchone():
            _exec(
                cur,
                "INSERT INTO trading_accounts (account_code, display_name, masked_name) VALUES (?, ?, ?)",
                (account_code, display_name, masked_name),
            )
    for name in reference_subjects:
        normalized = name.strip().lower()
        if not _exec(cur, "SELECT id FROM trading_business_subjects WHERE normalized_name = ?", (normalized,)).fetchone():
            _exec(
                cur,
                "INSERT INTO trading_business_subjects (name, normalized_name, created_by, updated_by) VALUES (?, ?, 'system', 'system')",
                (name, normalized),
            )
    for name in reference_strategies:
        normalized = name.strip().lower()
        if not _exec(cur, "SELECT id FROM trading_strategies WHERE normalized_name = ?", (normalized,)).fetchone():
            _exec(
                cur,
                "INSERT INTO trading_strategies (name, normalized_name, source, created_by, updated_by) VALUES (?, ?, 'template', 'system', 'system')",
                (name, normalized),
            )
    for exchange, product_code, asset_type, multiplier, tick in reference_specs:
        existing = _exec(
            cur,
            "SELECT id FROM trading_contract_specs WHERE exchange = ? AND product_code = ? AND asset_type = ?",
            (exchange, product_code, asset_type),
        ).fetchone()
        if not existing:
            _exec(
                cur,
                """
                INSERT INTO trading_contract_specs
                    (exchange, product_code, asset_type, contract_multiplier, price_tick, source)
                VALUES (?, ?, ?, ?, ?, 'sample_verified')
                """,
                (exchange, product_code, asset_type, multiplier, tick),
            )
    conn.commit()


def sync_trading_module_permissions(cur) -> None:
    """Add only missing permissions for the new menu; never overwrite explicit choices."""
    trading_codes = [code for group, code, _ in MODULES if group == "交易管理"]
    users = _exec(cur, "SELECT id, department, role FROM users").fetchall()
    for user in users:
        role = user["role"]
        department = user["department"]
        if role in {"管理员", "admin"}:
            permission = (1, 1, 1)
        elif role == "领导":
            permission = (1, 0, 0)
        elif department in {"期货组", "管理部门"}:
            permission = (1, 1, 0)
        else:
            continue
        for module_code in trading_codes:
            existing = _exec(
                cur,
                "SELECT id FROM module_permissions WHERE user_id = ? AND module_code = ?",
                (user["id"], module_code),
            ).fetchone()
            if not existing:
                _exec(
                    cur,
                    """
                    INSERT INTO module_permissions
                        (user_id, module_code, can_view, can_edit, can_sensitive)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user["id"], module_code, *permission),
                )


def ensure_admin_user(cur, name: str) -> int:
    _exec(cur, "SELECT id FROM users WHERE name = ?", (name,))
    admin = cur.fetchone()
    if admin:
        admin_id = admin["id"]
    elif _is_pg():
        cur.execute(
            "INSERT INTO users (name, username, department, password_hash, role) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, name, "管理部门", password_hash("admin"), "管理员"),
        )
        admin_id = cur.fetchone()["id"]
    else:
        cur.execute(
            "INSERT INTO users (name, username, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (name, name, "管理部门", password_hash("admin"), "管理员"),
        )
        admin_id = cur.lastrowid
    for _, module_code, _ in MODULES:
        _exec(
            cur,
            "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit, can_sensitive) VALUES (?, ?, 1, 1, 1)",
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
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'module_permissions'"
        )
        permission_columns = {row["column_name"] for row in cur.fetchall()}
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_guest INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cannot_change_password INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
        cur.execute("UPDATE users SET username = name WHERE username IS NULL OR username = ''")
        cur.execute("ALTER TABLE users ALTER COLUMN username SET NOT NULL")
        cur.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_name_key")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users(username)")
        cur.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_change_recommended INTEGER NOT NULL DEFAULT 0"
        )
        cur.execute("UPDATE users SET department = '管理部门' WHERE role IN ('管理员', 'admin') AND department != '管理部门'")
        cur.execute("ALTER TABLE module_permissions ADD COLUMN IF NOT EXISTS can_sensitive INTEGER NOT NULL DEFAULT 0")
        if "can_sensitive" not in permission_columns:
            cur.execute("UPDATE module_permissions SET can_sensitive = can_edit")
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
    if "username" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
        conn.execute("UPDATE users SET username = name WHERE username IS NULL OR username = ''")
    if "password_change_recommended" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_change_recommended INTEGER NOT NULL DEFAULT 0")
    unique_name = False
    for index_row in conn.execute("PRAGMA index_list(users)").fetchall():
        if not index_row["unique"]:
            continue
        columns = [row["name"] for row in conn.execute(f"PRAGMA index_info('{index_row['name']}')").fetchall()]
        if columns == ["name"]:
            unique_name = True
            break
    if unique_name:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            DROP TABLE IF EXISTS users_auth_migration;
            CREATE TABLE users_auth_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                username TEXT NOT NULL,
                department TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '启用',
                password_change_recommended INTEGER NOT NULL DEFAULT 0,
                is_guest INTEGER NOT NULL DEFAULT 0,
                cannot_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO users_auth_migration
                (id, name, username, department, password_hash, role, status,
                 password_change_recommended, is_guest, cannot_change_password,
                 created_at, updated_at)
            SELECT id, name, username, department, password_hash, role, status,
                   password_change_recommended, is_guest, cannot_change_password,
                   created_at, updated_at
            FROM users;
            DROP TABLE users;
            ALTER TABLE users_auth_migration RENAME TO users;
            """
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users(username)")
    conn.execute("UPDATE users SET department = '管理部门' WHERE role IN ('管理员', 'admin') AND department != '管理部门'")
    permission_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(module_permissions)").fetchall()
    }
    if "can_sensitive" not in permission_columns:
        conn.execute("ALTER TABLE module_permissions ADD COLUMN can_sensitive INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE module_permissions SET can_sensitive = can_edit")
    session_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(user_sessions)").fetchall()
    }
    if "expires_at" not in session_columns:
        conn.execute("ALTER TABLE user_sessions ADD COLUMN expires_at TEXT")


def migrate_order_finance_schema(conn) -> None:
    sync_status_sql = """
        CREATE TABLE IF NOT EXISTS order_finance_sync_status (
            id INTEGER PRIMARY KEY,
            last_success_at TEXT,
            changed_count INTEGER NOT NULL DEFAULT 0,
            source_version TEXT,
            last_attempt_slot TEXT,
            wps_refresh_token_ciphertext TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK (id = 1)
        )
    """
    columns = {
        "shipment_confirmed_date": "TEXT",
        "shipment_confirmed_by": "TEXT",
        "shipment_confirmed_at": "TEXT",
    }
    if _is_pg():
        cur = conn.cursor()
        cur.execute(sync_status_sql)
        cur.execute(
            "ALTER TABLE order_finance_sync_status "
            "ADD COLUMN IF NOT EXISTS wps_refresh_token_ciphertext TEXT"
        )
        cur.execute("ALTER TABLE order_finance_sync_status ENABLE ROW LEVEL SECURITY")
        cur.execute("REVOKE ALL ON TABLE order_finance_sync_status FROM anon, authenticated")
        for name, col_type in columns.items():
            cur.execute(f"ALTER TABLE order_finance_progress ADD COLUMN IF NOT EXISTS {name} {col_type}")
        cur.execute(
            """INSERT INTO order_finance_sync_status (id, changed_count)
               VALUES (1, 0) ON CONFLICT (id) DO NOTHING"""
        )
        conn.commit()
        return
    conn.execute(sync_status_sql)
    sync_status_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(order_finance_sync_status)").fetchall()
    }
    if "wps_refresh_token_ciphertext" not in sync_status_columns:
        conn.execute(
            "ALTER TABLE order_finance_sync_status "
            "ADD COLUMN wps_refresh_token_ciphertext TEXT"
        )
    conn.execute(
        "INSERT OR IGNORE INTO order_finance_sync_status (id, changed_count) VALUES (1, 0)"
    )
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(order_finance_progress)").fetchall()
    }
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE order_finance_progress ADD COLUMN {name} {col_type}")


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
        row = _exec(cur, "SELECT * FROM users WHERE username = ?", ("guest",)).fetchone()
        if row:
            guest_id = row["id"]
            _exec(
                cur,
                """
                UPDATE users
                SET name = 'guest', username = 'guest', role = 'guest', status = '启用',
                    is_guest = 1, cannot_change_password = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (guest_id,),
            )
        elif _is_pg():
            cur.execute(
                """
                INSERT INTO users (name, username, department, password_hash, role, status, is_guest, cannot_change_password)
                VALUES (%s, %s, %s, %s, %s, %s, 1, 1)
                RETURNING id
                """,
                ("guest", "guest", "访客", password_hash(secrets.token_urlsafe(16)), "guest", "启用"),
            )
            guest_id = cur.fetchone()["id"]
        else:
            cur.execute(
                """
                INSERT INTO users (name, username, department, password_hash, role, status, is_guest, cannot_change_password)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1)
                """,
                ("guest", "guest", "访客", password_hash(secrets.token_urlsafe(16)), "guest", "启用"),
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
