from test_iron_ore_source_ingest import _arrival_workbook, _inventory_workbook


def _seed_legacy(db, week_start, value, metric="inventory", product="PB粉"):
    from datetime import date, timedelta

    start = date.fromisoformat(week_start)
    with db.connect() as conn:
        cur = conn.cursor()
        batch_id = db._last_insert_id(cur, """INSERT INTO dv_integration_batches
            (file_names, status, point_count, apparent_demand_count, validation_summary, created_by)
            VALUES (?, 'committed', 1, 0, '{}', ?)""", ("legacy.xlsx", "tester"))
        db._exec(cur, """INSERT INTO dv_integrated_points
            (batch_id, week_start, week_end, business_year, business_week, week_label,
             display_date, metric_type, source_country, product, category, mainstream_status,
             value, unit, source_file, source_sheet, source_section, is_calculable, validation_status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                batch_id, week_start, (start + timedelta(days=6)).isoformat(), start.year,
                36 if week_start == "2026-08-31" else 35, f"{start.year} W36", week_start,
                metric, "澳洲", product, "粉矿", "主流", value, "万吨", "legacy.xlsx", "库存", "总计", 1, "ok", "",
            ))


def test_template_registration_is_idempotent_and_snapshot_keeps_actual_separate(tmp_path, monkeypatch):
    from backend.app import db
    from backend.app.iron_ore_weekly_report import build_report_snapshot, register_builtin_templates

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    first = register_builtin_templates()
    second = register_builtin_templates()
    assert first["id"] == second["id"]
    assert first["version"] == "V1.0"

    _seed_legacy(db, "2026-08-24", 100)
    _seed_legacy(db, "2026-08-31", 90)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, """INSERT INTO dv_arrival_facts
            (package_id, arrival_kind, method, observed_date, week_start, port_name, scope_type, slice_type,
             dimension, product, category, grade, source_country, mainstream_status, value, value_status, unit,
             source_file, source_sheet, mapping_version)
            VALUES (?, 'actual', 'reported_47_port', '2026-08-30', '2026-08-24', '南通', 'port', 'country',
                    '总计', '', '', '', '澳洲', '', 50, 'numeric', '万吨', 'actual.xlsx', '国家', 'v1')""", ("pkg-1",))
        db._exec(cur, """INSERT INTO dv_arrival_facts
            (package_id, arrival_kind, method, observed_date, week_start, port_name, scope_type, slice_type,
             dimension, product, category, grade, source_country, mainstream_status, value, value_status, unit,
             source_file, source_sheet, mapping_version)
            VALUES (?, 'actual', 'reported_47_port', '2026-09-06', '2026-08-31', '南通', 'port', 'country',
                    '总计', '', '', '', '澳洲', '', 60, 'numeric', '万吨', 'actual.xlsx', '国家', 'v1')""", ("pkg-1",))

    snapshot = build_report_snapshot("2026-W36", first["id"], "tester")
    assert snapshot["report_week"] == "2026-W36"
    assert snapshot["validation"]["actual_arrival"]["current_count"] == 1
    assert snapshot["validation"]["legacy_apparent_demand"]["current_count"] == 0
    assert all(row["arrival_kind"] == "actual" for row in snapshot["input"]["arrival_actual"])


def test_report_input_keeps_legacy_arrival_and_demand_history(tmp_path, monkeypatch):
    from backend.app import db
    from backend.app.iron_ore_weekly_report import _load_report_input

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()

    _seed_legacy(db, "2026-08-24", 80, metric="arrival")
    _seed_legacy(db, "2026-08-31", 90, metric="arrival")
    _seed_legacy(db, "2026-08-24", 70, metric="apparent_demand")
    _seed_legacy(db, "2026-08-31", 75, metric="apparent_demand")

    loaded = _load_report_input("2026-W36")
    assert len(loaded["input"]["history_arrival_estimated"]) == 2
    assert len(loaded["input"]["history_apparent_demand"]) == 2


def test_report_generation_is_idempotent_and_writes_versioned_pdf(tmp_path, monkeypatch):
    from backend.app import db
    from backend.app.iron_ore_weekly_report import generate_report, register_builtin_templates

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    template = register_builtin_templates()
    _seed_legacy(db, "2026-08-24", 100)
    _seed_legacy(db, "2026-08-31", 90)
    first = generate_report("2026-W36", template["version"], "tester")
    second = generate_report("2026-W36", template["version"], "tester")
    assert first["run_id"] == second["run_id"]
    assert first["revision_no"] == 1
    assert first["status"] == "succeeded"
    assert first["file_path"].endswith("模板V1.0_R1.pdf")
