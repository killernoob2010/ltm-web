from test_iron_ore_source_ingest import _arrival_workbook, _inventory_workbook


def test_v2_import_preserves_legacy_summary_and_inserts_detail_facts(tmp_path, monkeypatch):
    from backend.app import db
    from backend.app.data_visualization import _import_integrated_v2, _parse_integrated_excel
    from backend.app.iron_ore_source_ingest import build_v2_workbook, parse_mysteel_source_files

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    inventory = tmp_path / "库存.xlsx"
    arrival = tmp_path / "到港.xlsx"
    _inventory_workbook(inventory)
    _arrival_workbook(arrival)
    package = parse_mysteel_source_files([inventory, arrival])
    workbook = tmp_path / "v2.xlsx"
    workbook.write_bytes(build_v2_workbook(package))

    with db.connect() as conn:
        cur = conn.cursor()
        batch_id = db._last_insert_id(cur, """INSERT INTO dv_integration_batches
            (file_names, status, point_count, apparent_demand_count, validation_summary, created_by)
            VALUES (?, 'committed', 1, 0, '{}', ?)""", ("old.xlsx", "tester"))
        db._exec(cur, """INSERT INTO dv_integrated_points
            (batch_id, week_start, week_end, business_year, business_week, week_label,
             display_date, metric_type, source_country, product, category, mainstream_status,
             value, unit, source_file, source_sheet, source_section, is_calculable, validation_status, note)
            VALUES (?, '2026-08-24', '2026-08-30', 2026, 35, '2026 W35', '2026-08-30',
                    'inventory', '澳洲', 'PB粉', '粉矿', '主流', 10, '万吨', 'old.xlsx', '库存', '总计', 1, 'ok', '')""",
            (batch_id,))

    parsed = _parse_integrated_excel(workbook)
    assert parsed["version"] == "v2"
    result = _import_integrated_v2(parsed, workbook.name, "tester")
    assert result["duplicate"] is False
    duplicate = _import_integrated_v2(parsed, workbook.name, "tester")
    assert duplicate["duplicate"] is True
    with db.connect() as conn:
        cur = conn.cursor()
        legacy = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_integrated_points").fetchone()["c"]
        detail = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_port_inventory_facts").fetchone()["c"]
        arrivals = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_arrival_facts WHERE arrival_kind = 'actual'").fetchone()["c"]
    assert legacy >= 1
    assert detail > 0
    assert arrivals > 0


def test_overlapping_packages_do_not_duplicate_facts_and_conflicts_roll_back(tmp_path, monkeypatch):
    import pytest
    from backend.app import db
    from backend.app.iron_ore_source_ingest import SourcePackage, store_source_package_facts

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    db.init_db()
    from backend.app.iron_ore_source_ingest import parse_mysteel_source_files
    source = tmp_path / "inventory.xlsx"
    _inventory_workbook(source)
    row = dict(parse_mysteel_source_files([source]).inventory_port_product[0], value=10)
    first = SourcePackage(inventory_port_product=[row, dict(row)])
    assert store_source_package_facts(first)["inventory_port_product"] == 1
    second = SourcePackage(inventory_port_product=[dict(row, source_file="b.xlsx")])
    assert store_source_package_facts(second)["inventory_port_product"] == 0
    conflict = SourcePackage(inventory_port_product=[dict(row, value=11)])
    with pytest.raises(ValueError, match="数值冲突"):
        store_source_package_facts(conflict)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM dv_port_inventory_facts").fetchone()["COUNT(*)"] == 1
        assert conn.execute("SELECT COUNT(*) FROM dv_source_packages WHERE package_id = ?", (conflict.package_id,)).fetchone()["COUNT(*)"] == 0


def test_v2_summary_bulk_merge_preserves_last_value_and_blank_protection(tmp_path, monkeypatch):
    from datetime import date
    from backend.app import db
    from backend.app import data_visualization as dv

    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'data' / 'app.db')
    db.init_db()
    base = dv._make_point(week_start=date(2026, 8, 31), display_date=date(2026, 9, 1),
                         metric_type='inventory', source_country='澳洲', product='PB粉', category='粉矿',
                         value=10, source_file='source.xlsx', source_sheet='粗粉', source_section='总计', is_calculable=False)
    rows = [dict(base, product=f'品种{i}') for i in range(100)]
    queries = []
    original = db._exec
    def counted(cur, sql, params=()):
        if 'SELECT' in sql and 'FROM dv_integrated_points' in sql: queries.append(sql)
        return original(cur, sql, params)
    monkeypatch.setattr(db, '_exec', counted)
    with db.connect() as conn:
        result = dv._merge_integrated_points_v2_in_connection(conn.cursor(), rows + [dict(rows[0], value=12), dict(rows[0], value=None)], 'source.xlsx', 'tester')
    assert (result['inserted'], result['updated'], result['skipped']) == (100, 1, 1)
    assert len(queries) <= 2
    with db.connect() as conn:
        result = dv._merge_integrated_points_v2_in_connection(conn.cursor(), [dict(rows[0], value=14), dict(rows[0], value=None)], 'update.xlsx', 'tester')
        saved = db._exec(conn.cursor(), "SELECT value FROM dv_integrated_points WHERE product='品种0'").fetchall()
    assert [r['value'] for r in saved] == [14]
    assert (result['inserted'], result['updated'], result['skipped']) == (0, 1, 1)
