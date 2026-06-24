import hashlib
import os
import re
import secrets
from contextlib import contextmanager
import psycopg2
import psycopg2.extras
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

MODULES = [
    ("台账管理", "sh_junneng", "上海均能台账"),
    ("台账管理", "steel_export", "钢材出口套保台账"),
    ("台账管理", "subsidiary_hedging", "子公司套保台账"),
    ("台账管理", "option_trading", "期权交易台账"),
    ("信息预警管理", "info_summary", "实时信息汇总"),
    ("信息预警管理", "risk_alert", "风险预警"),
    ("信息预警管理", "mid_event_monitor", "事中风险监控"),
    ("后台管理", "user_management", "用户管理"),
    ("后台管理", "data_management", "数据管理"),
    ("数据可视化管理", "data_visualization_data", "图表数据管理"),
    ("数据可视化管理", "data_visualization_chart", "数据展示"),
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

def password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


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
                info_type TEXT NOT NULL,
                contract_code TEXT NOT NULL,
                direction TEXT NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
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
                open_volume DOUBLE PRECISION NOT NULL,
                open_date TEXT,
                close_price DOUBLE PRECISION,
                close_volume DOUBLE PRECISION,
                close_date TEXT,
                status TEXT NOT NULL DEFAULT '未平仓',
                is_closed INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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
                info_type TEXT NOT NULL,
                contract_code TEXT NOT NULL,
                direction TEXT NOT NULL,
                volume REAL NOT NULL,
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
                open_volume REAL NOT NULL,
                open_date TEXT,
                close_price REAL,
                close_volume REAL,
                close_date TEXT,
                status TEXT NOT NULL DEFAULT '未平仓',
                is_closed INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
            )
        migrate_cache_schema(conn)
        migrate_alert_schema(conn)
        migrate_sh_junneng_schema(conn)

        _exec(cur, "SELECT id FROM users WHERE name = ?", ("管理员",))
        admin = cur.fetchone()
        if not admin:
            if _is_pg():
                cur.execute(
                    "INSERT INTO users (name, department, password_hash, role) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    ("管理员", "管理部门", password_hash("admin"), "管理员"),
                )
                admin_id = cur.fetchone()["id"]
            else:
                cur.execute(
                    "INSERT INTO users (name, department, password_hash, role) "
                    "VALUES (?, ?, ?, ?)",
                    ("管理员", "管理部门", password_hash("admin"), "管理员"),
                )
                admin_id = cur.lastrowid
            for _, module_code, _ in MODULES:
                _exec(cur,
                    "INSERT INTO module_permissions (user_id, module_code, can_view, can_edit) "
                    "VALUES (?, ?, 1, 1)",
                    (admin_id, module_code),
                )
            conn.commit()


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


def migrate_sh_junneng_schema(conn) -> None:
    if _is_pg():
        cur = conn.cursor()
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sh_junneng_contract
            ON sh_junneng_trades(contract_month);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_open_date
            ON sh_junneng_trades(open_date);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_date
            ON sh_junneng_trades(close_date);

            CREATE INDEX IF NOT EXISTS idx_sh_junneng_status
            ON sh_junneng_trades(status);
            """
        )
        conn.commit()
        return
    # SQLite: use executescript for multi-statement
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_sh_junneng_contract
        ON sh_junneng_trades(contract_month);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_open_date
        ON sh_junneng_trades(open_date);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_close_date
        ON sh_junneng_trades(close_date);

        CREATE INDEX IF NOT EXISTS idx_sh_junneng_status
        ON sh_junneng_trades(status);
        """
    )


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        cur = conn.cursor()
        _exec(cur,
            "INSERT INTO user_sessions (user_id, token) VALUES (?, ?)",
            (user_id, token),
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
            SELECT u.*
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.status = '活跃' AND u.status = '启用'
            """,
            (token,),
        )
        return cur.fetchone()


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
