from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_field_contract_contains_exactly_a_to_ay():
    from app.spot_ledger import FIELD_DEFINITIONS

    assert [item["code"] for item in FIELD_DEFINITIONS] == [
        "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO", "AP", "AQ", "AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY",
    ]
    assert all(item["source_rule"] and item["control"] for item in FIELD_DEFINITIONS)


def test_contract_mapping_uses_signed_date_and_settlement_after_close():
    from app.spot_ledger import normalize_sales_contract_record

    record = normalize_sales_contract_record({
        "detail_id": "D-1", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "山东组", "profit_group": "唐山组",
        "contract_number": "C-1", "product_name": "铁矿石", "signed_date": "2026-08-24",
        "contract_quantity": 120, "settlement_quantity": 100, "is_closed": True,
        "business_category_code": "B09", "demander": "客户A",
    })
    assert record["U"] == "2026-08-24"
    assert record["L"] == record["X"] == 100
    assert record["D"] == "船货-落地"
    assert record["E"] == "山东组" and record["AP"] == "唐山组"
    assert record["AQ"] == "是"


def test_missing_required_fields_respects_land_contract_condition():
    from app.spot_ledger import missing_required_fields

    record = {"D": "船货-落地", "C": "自主建仓", "K": "船A", "N": 0, "O": 0, "Y": 0, "P": "是", "long_contract_object": ""}
    assert "长协对象" in missing_required_fields(record)
    record["P"] = "否"
    assert "长协对象" not in missing_required_fields(record)


def test_zero_is_valid_but_negative_quantity_and_cost_are_not():
    from app.spot_ledger import missing_required_fields, validate_record_values

    record = {"C": "自主建仓", "K": "船A", "N": 0, "O": 0, "Y": 0, "D": "现货-市场加价", "P": ""}
    assert missing_required_fields(record) == []
    errors = validate_record_values({**record, "N": -1, "Y": -0.1})
    assert "N" in errors and "Y" in errors


def test_system_conversion_preserves_unknown_type_and_normalizes_placeholders():
    from app.spot_ledger import normalize_sales_contract_record

    record = normalize_sales_contract_record({
        "detail_id": "D-2", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组",
        "contract_number": "C-2", "product_name": "铁矿石", "signed_date": "2026-08-01",
        "contract_quantity": 10, "business_category_code": "B-UNKNOWN",
        "vessel_name": "--", "sales_execution": "920109_已完成",
    })
    assert record["K"] == ""
    assert record["AG"] == "已完成"
    assert record["D"] == "B-UNKNOWN"
    assert any(error["type"] == "conversion_mapping" for error in record["sync_errors"])


@pytest.mark.parametrize(
    ("source_category", "expected_type"),
    [
        ("贸易-港口现货-市场加价-B07", "现货-市场加价"),
        ("贸易-港口现货-背对背-B06", "现货-背对背"),
        ("贸易-代理落地-B09", "船货-落地"),
        ("贸易-落地-固定价-B05", "船货-落地"),
        ("B0701", "现货-市场加价"),
        ("B0601", "现货-背对背"),
        ("B0901", "船货-落地"),
        ("B0501", "船货-落地"),
    ],
)
def test_confirmed_source_business_categories_map_without_false_sync_errors(source_category, expected_type):
    from app.spot_ledger import normalize_sales_contract_record

    record = normalize_sales_contract_record({
        "detail_id": "D-SOURCE", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组",
        "contract_number": "C-SOURCE", "product_name": "铁矿石", "signed_date": "2026-08-25",
        "contract_quantity": "20", "business_category_code": source_category,
    })

    assert record["D"] == expected_type
    assert not any(error["type"] == "conversion_mapping" and error["field"] == "D" for error in record["sync_errors"])


def test_schema_is_idempotent_and_contains_all_business_columns(tmp_path, monkeypatch):
    from app import db
    from app.spot_ledger import FIELD_DEFINITIONS, initialize_schema

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "spot-ledger.db")
    with db.connect() as conn:
        initialize_schema(conn)
        initialize_schema(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(spot_ledger_records)").fetchall()}
    assert {item["code"] for item in FIELD_DEFINITIONS}.issubset(columns)
    assert {
        "record_id", "source_detail_id", "source_closed_state", "is_active", "missing_fields", "sync_error_summary",
    }.issubset(columns)


def test_postgres_schema_uses_supabase_safe_idempotent_ddl(monkeypatch):
    from app import db
    from app.spot_ledger import initialize_schema

    statements = []

    class RecordingCursor:
        def execute(self, statement, params=None):
            statements.append(" ".join(statement.split()))

    class RecordingConnection:
        def cursor(self):
            return RecordingCursor()

    monkeypatch.setattr(db, "_is_pg", lambda: True)
    initialize_schema(RecordingConnection())
    sql = "\n".join(statements)

    assert "CREATE TABLE IF NOT EXISTS spot_ledger_records" in sql
    assert "DOUBLE PRECISION" in sql
    assert "AUTOINCREMENT" not in sql
    assert "PRAGMA" not in sql
    assert "ALTER TABLE spot_ledger_records ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE spot_ledger_sync_runs ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE spot_ledger_records, spot_ledger_sync_runs FROM anon, authenticated" in sql


def test_spot_ledger_is_a_visible_trade_module_with_sensitive_admin_permission(tmp_path, monkeypatch):
    from app import db, permissions

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "auth.db")
    db.init_db()
    assert ("贸易台账管理", "spot_ledger", "现货业务台账管理") in db.MODULES
    assert "spot_ledger" in permissions.ACTIVE_BUSINESS_MODULES
    assert "spot_ledger" in permissions.DEPARTMENT_MODULES["贸易处"]
    with db.connect() as conn:
        admin = conn.execute("SELECT id FROM users WHERE role = '管理员' ORDER BY id LIMIT 1").fetchone()
        permission = conn.execute(
            "SELECT can_view, can_edit, can_sensitive FROM module_permissions WHERE user_id = ? AND module_code = 'spot_ledger'",
            (admin["id"],),
        ).fetchone()
    assert tuple(permission) == (1, 1, 1)
