import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_report_schema_creates_source_and_report_tables(tmp_path, monkeypatch):
    from app import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "report.db")
    db.init_db()

    with db.connect() as conn:
        cursor = conn.cursor()
        if db._is_pg():
            rows = db._exec(
                cursor,
                "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'",
            ).fetchall()
        else:
            rows = db._exec(
                cursor,
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
    names = {row["name"] for row in rows}
    assert {
        "dv_source_files",
        "dv_source_packages",
        "dv_port_inventory_facts",
        "dv_inventory_summary_facts",
        "dv_inventory_grade_facts",
        "dv_inventory_mainstream_facts",
        "dv_arrival_facts",
        "dv_report_templates",
        "dv_report_snapshots",
        "dv_report_runs",
        "dv_report_artifacts",
    } <= names

    with db.connect() as conn:
        cursor = conn.cursor()
        db._exec(
            cursor,
            "INSERT INTO dv_report_templates (template_key, version, name) VALUES (?, ?, ?)",
            ("test_series", "V1.0", "测试模板 1"),
        )
        db._exec(
            cursor,
            "INSERT INTO dv_report_templates (template_key, version, name) VALUES (?, ?, ?)",
            ("test_series", "V1.1", "测试模板 2"),
        )
        rows = db._exec(
            cursor,
            "SELECT version FROM dv_report_templates WHERE template_key = ? ORDER BY version",
            ("test_series",),
        ).fetchall()
    assert [row["version"] for row in rows] == ["V1.0", "V1.1"]
