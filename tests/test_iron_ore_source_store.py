from test_iron_ore_source_ingest import _arrival_workbook, _inventory_workbook


def test_archive_and_store_source_package_keeps_blanks_and_is_idempotent(tmp_path, monkeypatch):
    from backend.app import db
    from backend.app.iron_ore_source_ingest import (
        archive_source_package,
        parse_mysteel_source_files,
        store_source_package_facts,
    )

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    inventory = tmp_path / "库存.xlsx"
    arrival = tmp_path / "到港.xlsx"
    _inventory_workbook(inventory)
    _arrival_workbook(arrival)
    package = parse_mysteel_source_files([inventory, arrival])

    archived = archive_source_package(package, "tester")
    first = store_source_package_facts(package, archived["source_file_ids"], "tester")
    second = store_source_package_facts(package, archived["source_file_ids"], "tester")

    assert first["inserted"] > 0
    assert second["duplicate"] is True
    with db.connect() as conn:
        cur = conn.cursor()
        facts = db._exec(cur, "SELECT * FROM dv_port_inventory_facts WHERE product = 'PB粉'").fetchall()
        missing = db._exec(cur, "SELECT * FROM dv_arrival_facts WHERE dimension = '巴西' AND port_name = '日照'").fetchone()
        source = db._exec(cur, "SELECT archive_path FROM dv_source_files ORDER BY id LIMIT 1").fetchone()
    assert facts and facts[0]["package_id"] == package.package_id
    assert missing["value"] is None and missing["value_status"] == "missing"
    assert source and source["archive_path"].startswith(str(tmp_path / "data" / "iron_ore_source_archive"))


def test_prepare_source_package_exports_without_activating_legacy_points(tmp_path, monkeypatch):
    from backend.app import db
    from backend.app.data_visualization import _prepare_source_package

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    inventory = tmp_path / "库存.xlsx"
    _inventory_workbook(inventory)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, """INSERT INTO dv_integration_batches
            (file_names, status, point_count, apparent_demand_count, validation_summary, created_by)
            VALUES (?, 'committed', 1, 0, '{}', ?)""", ("legacy.xlsx", "tester"))
        batch_id = db.last_insert_id(conn)
        db._exec(cur, """INSERT INTO dv_integrated_points
            (batch_id, week_start, week_end, business_year, business_week, week_label,
             display_date, metric_type, source_country, product, category, mainstream_status,
             value, unit, source_file, source_sheet, source_section, is_calculable, validation_status, note)
            VALUES (?, '2026-08-24', '2026-08-30', 2026, 35, '2026 W35', '2026-08-24',
                    'inventory', '澳洲', 'PB粉', '粉矿', '主流', 10, '万吨', 'legacy.xlsx', '库存', '总计', 1, 'ok', '')""",
            (batch_id,))
    package, _archived, output_path, summary = _prepare_source_package([inventory], [inventory.name], "tester")
    assert output_path.exists() and summary["package_id"] == package.package_id
    with db.connect() as conn:
        cur = conn.cursor()
        count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_integrated_points").fetchone()["c"]
        status = db._exec(cur, "SELECT status FROM dv_source_packages WHERE package_id = ?", (package.package_id,)).fetchone()["status"]
    assert count == 1
    assert status == "prepared"
