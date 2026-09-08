"""Additive schema for source archives and reproducible weekly reports."""

from typing import Any


_TEMPLATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS dv_report_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_key TEXT NOT NULL,
        version TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        is_default INTEGER NOT NULL DEFAULT 0,
        change_summary TEXT NOT NULL DEFAULT '',
        config_json TEXT NOT NULL DEFAULT '{}',
        config_sha256 TEXT NOT NULL DEFAULT '',
        renderer_version TEXT NOT NULL DEFAULT '',
        rules_reference TEXT NOT NULL DEFAULT '',
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(template_key, version)
    )
    """


SQLITE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS dv_source_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_name TEXT NOT NULL,
        sha256 TEXT NOT NULL UNIQUE,
        archive_path TEXT NOT NULL,
        template_type TEXT NOT NULL,
        template_version TEXT,
        source_date_start TEXT,
        source_date_end TEXT,
        uploaded_by TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_source_packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id TEXT NOT NULL UNIQUE,
        structure_version TEXT NOT NULL,
        parser_version TEXT NOT NULL,
        mapping_version TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'prepared',
        source_file_ids TEXT NOT NULL DEFAULT '[]',
        validation_json TEXT NOT NULL DEFAULT '{}',
        output_path TEXT,
        output_sha256 TEXT,
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_port_inventory_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id TEXT NOT NULL,
        observed_date TEXT NOT NULL,
        week_start TEXT NOT NULL,
        sample_name TEXT NOT NULL DEFAULT '',
        port_name TEXT NOT NULL DEFAULT '',
        region TEXT NOT NULL DEFAULT '',
        scope_type TEXT NOT NULL,
        raw_product TEXT NOT NULL DEFAULT '',
        product TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        source_country TEXT NOT NULL DEFAULT '',
        mainstream_status TEXT NOT NULL DEFAULT '',
        value REAL,
        value_status TEXT NOT NULL DEFAULT 'missing',
        unit TEXT NOT NULL DEFAULT '万吨',
        source_file TEXT NOT NULL DEFAULT '',
        source_sheet TEXT NOT NULL DEFAULT '',
        source_row INTEGER,
        source_column INTEGER,
        source_cell TEXT NOT NULL DEFAULT '',
        mapping_version TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_inventory_summary_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id TEXT NOT NULL,
        observed_date TEXT NOT NULL,
        week_start TEXT NOT NULL,
        sample_name TEXT NOT NULL DEFAULT '',
        port_name TEXT NOT NULL DEFAULT '',
        region TEXT NOT NULL DEFAULT '',
        scope_type TEXT NOT NULL,
        metric TEXT NOT NULL,
        value REAL,
        value_status TEXT NOT NULL DEFAULT 'missing',
        unit TEXT NOT NULL DEFAULT '万吨',
        source_file TEXT NOT NULL DEFAULT '',
        source_sheet TEXT NOT NULL DEFAULT '',
        source_row INTEGER,
        source_column INTEGER,
        source_cell TEXT NOT NULL DEFAULT '',
        mapping_version TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_inventory_grade_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id TEXT NOT NULL,
        observed_date TEXT NOT NULL,
        week_start TEXT NOT NULL,
        sample_name TEXT NOT NULL DEFAULT '',
        port_name TEXT NOT NULL DEFAULT '',
        region TEXT NOT NULL DEFAULT '',
        scope_type TEXT NOT NULL,
        raw_grade TEXT NOT NULL DEFAULT '',
        grade TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        value REAL,
        value_status TEXT NOT NULL DEFAULT 'missing',
        unit TEXT NOT NULL DEFAULT '万吨',
        source_file TEXT NOT NULL DEFAULT '',
        source_sheet TEXT NOT NULL DEFAULT '',
        source_row INTEGER,
        source_column INTEGER,
        source_cell TEXT NOT NULL DEFAULT '',
        mapping_version TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_inventory_mainstream_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id TEXT NOT NULL,
        observed_date TEXT NOT NULL,
        week_start TEXT NOT NULL,
        sample_name TEXT NOT NULL DEFAULT '',
        port_name TEXT NOT NULL DEFAULT '',
        region TEXT NOT NULL DEFAULT '',
        scope_type TEXT NOT NULL,
        raw_product TEXT NOT NULL DEFAULT '',
        product TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        source_country TEXT NOT NULL DEFAULT '',
        value REAL,
        value_status TEXT NOT NULL DEFAULT 'missing',
        unit TEXT NOT NULL DEFAULT '万吨',
        source_file TEXT NOT NULL DEFAULT '',
        source_sheet TEXT NOT NULL DEFAULT '',
        source_row INTEGER,
        source_column INTEGER,
        source_cell TEXT NOT NULL DEFAULT '',
        mapping_version TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_arrival_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id TEXT NOT NULL,
        arrival_kind TEXT NOT NULL,
        method TEXT NOT NULL DEFAULT '',
        observed_date TEXT NOT NULL,
        week_start TEXT NOT NULL,
        port_name TEXT NOT NULL DEFAULT '',
        scope_type TEXT NOT NULL,
        slice_type TEXT NOT NULL,
        dimension TEXT NOT NULL DEFAULT '',
        product TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        grade TEXT NOT NULL DEFAULT '',
        source_country TEXT NOT NULL DEFAULT '',
        mainstream_status TEXT NOT NULL DEFAULT '',
        value REAL,
        value_status TEXT NOT NULL DEFAULT 'missing',
        unit TEXT NOT NULL DEFAULT '万吨',
        source_file TEXT NOT NULL DEFAULT '',
        source_sheet TEXT NOT NULL DEFAULT '',
        source_row INTEGER,
        source_column INTEGER,
        source_cell TEXT NOT NULL DEFAULT '',
        mapping_version TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    _TEMPLATE_TABLE_SQL,
    """
    CREATE TABLE IF NOT EXISTS dv_report_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_week TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,
        input_json TEXT NOT NULL,
        validation_json TEXT NOT NULL DEFAULT '{}',
        source_batch_ids TEXT NOT NULL DEFAULT '[]',
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(report_week, input_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_report_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_week TEXT NOT NULL,
        template_id INTEGER NOT NULL,
        template_version TEXT NOT NULL,
        snapshot_id INTEGER NOT NULL,
        revision_no INTEGER NOT NULL DEFAULT 1,
        job_id TEXT UNIQUE,
        status TEXT NOT NULL DEFAULT 'queued',
        created_by TEXT,
        error_message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT,
        UNIQUE(report_week, template_id, snapshot_id, revision_no),
        FOREIGN KEY (template_id) REFERENCES dv_report_templates(id),
        FOREIGN KEY (snapshot_id) REFERENCES dv_report_snapshots(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_report_artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL UNIQUE,
        file_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        file_size INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (run_id) REFERENCES dv_report_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dv_report_file_contents (
        run_id INTEGER PRIMARY KEY,
        content BLOB NOT NULL,
        FOREIGN KEY (run_id) REFERENCES dv_report_runs(id)
    )
    """,
    "CREATE TABLE IF NOT EXISTS dv_source_file_contents (file_sha256 TEXT PRIMARY KEY, content BLOB NOT NULL)",
    "CREATE TABLE IF NOT EXISTS dv_source_package_contents (package_id TEXT PRIMARY KEY, content BLOB NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_dv_source_files_type_date ON dv_source_files(template_type, source_date_end)",
    "CREATE INDEX IF NOT EXISTS idx_dv_source_packages_status ON dv_source_packages(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dv_port_inventory_facts_week ON dv_port_inventory_facts(week_start, port_name, product)",
    "CREATE INDEX IF NOT EXISTS idx_dv_inventory_summary_facts_week ON dv_inventory_summary_facts(week_start, port_name, metric)",
    "CREATE INDEX IF NOT EXISTS idx_dv_inventory_grade_facts_week ON dv_inventory_grade_facts(week_start, port_name, grade, category)",
    "CREATE INDEX IF NOT EXISTS idx_dv_arrival_facts_week ON dv_arrival_facts(week_start, arrival_kind, port_name, slice_type)",
    "CREATE INDEX IF NOT EXISTS idx_dv_report_runs_week ON dv_report_runs(report_week, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dv_report_runs_status ON dv_report_runs(status, created_at DESC)",
)


POSTGRES_STATEMENTS = tuple(
    statement.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    .replace("value REAL", "value DOUBLE PRECISION")
    .replace("content BLOB", "content BYTEA")
    .replace("TEXT NOT NULL DEFAULT '{}'", "TEXT NOT NULL DEFAULT '{}'")
    .replace("TEXT NOT NULL DEFAULT '[]'", "TEXT NOT NULL DEFAULT '[]'")
    for statement in SQLITE_STATEMENTS
)


def ensure_report_schema(conn: Any, postgres: bool = False) -> None:
    """Create report tables without changing existing analytical tables."""
    if postgres:
        cursor = conn.cursor()
        for statement in POSTGRES_STATEMENTS:
            cursor.execute(statement)
        for table in ("dv_report_file_contents", "dv_source_file_contents", "dv_source_package_contents"):
            cursor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            cursor.execute(f"REVOKE ALL ON {table} FROM PUBLIC, anon, authenticated")
        return
    _migrate_sqlite_template_table(conn)
    for statement in SQLITE_STATEMENTS:
        conn.execute(statement)


def _migrate_sqlite_template_table(conn: Any) -> None:
    """Allow multiple versions within one template series.

    V1.0 initially used a single-column UNIQUE constraint on ``template_key``.
    Rebuild only that additive report table when an older local database is
    encountered; foreign-key enforcement is restored before returning.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'dv_report_templates'"
    ).fetchone()
    schema_sql = row[0] if row else ""
    if "template_key TEXT NOT NULL UNIQUE" not in schema_sql:
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE dv_report_templates RENAME TO dv_report_templates_v1")
    conn.execute(_TEMPLATE_TABLE_SQL)
    conn.execute(
        """INSERT INTO dv_report_templates
           (id, template_key, version, name, status, is_default, change_summary,
            config_json, config_sha256, renderer_version, rules_reference,
            created_by, created_at, updated_at)
           SELECT id, template_key, version, name, status, is_default, change_summary,
                  config_json, config_sha256, renderer_version, rules_reference,
                  created_by, created_at, updated_at
           FROM dv_report_templates_v1"""
    )
    conn.execute("DROP TABLE dv_report_templates_v1")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
