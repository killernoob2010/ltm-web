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
    from app.spot_ledger import normalize_sales_contract_record, record_to_public

    record = normalize_sales_contract_record({
        "detail_id": "D-1", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "山东组", "profit_group": "唐山组",
        "contract_number": "C-1", "product_name": "铁矿石", "signed_date": "2026-08-24",
        "contract_quantity": 120, "settlement_quantity": 100, "is_closed": True,
        "business_category_code": "B09", "demander": "客户A",
    })
    assert record["U"] == "2026-08-24"
    assert record["L"] == record["X"] == 100
    assert record["D"] == "B09"
    assert record_to_public(record)["is_land_goods"] is True
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


def test_confirmed_history_dictionaries_map_operation_title_and_product_category():
    from app.spot_ledger import normalize_sales_contract_record

    record = normalize_sales_contract_record({
        "detail_id": "D-DICTIONARY", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组",
        "contract_number": "C-DICTIONARY", "product_name": "PB粉",
        "product_category": "PB粉", "operation_title": "天津建龙钢铁实业有限公司",
        "signed_date": "2026-08-25", "contract_quantity": 20,
        "business_category_code": "B0701",
    })

    assert record["F"] == "天津建龙"
    assert record["AU"] == "主流"
    assert not any(error["field"] in {"F", "AU"} for error in record["sync_errors"])


@pytest.mark.parametrize(
    ("source_supplier", "expected_supplier"),
    [
        ("上海德天钢铁发展有限公司", "上海德天"),
        ("厦门建发矿业资源有限公司", "建发"),
        ("宁夏建龙龙祥钢铁有限公司", "宁夏特钢"),
        ("宁波凯创物产有限公司", "凯创"),
        ("山能（济南）智慧投资有限公司", "山能（济南）智慧-过"),
        ("张家港保税区沙钢矿产品有限公司", "沙钢矿产"),
        ("杭州热联集团股份有限公司", "热联"),
        ("浙江杭钢国贸有限公司", "杭钢国贸"),
        ("瑞钢联集团有限公司", "瑞钢联"),
        ("鞍钢集团国际经济贸易有限公司本溪分公司", "本钢"),
    ],
)
def test_confirmed_history_supplier_dictionary_maps_repeated_exact_matches(source_supplier, expected_supplier):
    from app.spot_ledger import normalize_sales_contract_record, record_to_public

    record = normalize_sales_contract_record({
        "detail_id": "D-SUPPLIER-DICTIONARY", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组",
        "contract_number": "C-SUPPLIER-DICTIONARY", "product_name": "PB粉",
        "product_category": "PB粉", "operation_title": "天津建龙钢铁实业有限公司",
        "supplier": source_supplier, "signed_date": "2026-08-25", "contract_quantity": 20,
        "business_category_code": "B0701",
    })

    assert record["Q"] == source_supplier
    assert record_to_public(record)["supplier_display_name"] == expected_supplier
    assert not any(error["field"] == "Q" for error in record["sync_errors"])


@pytest.mark.parametrize(
    ("source_category", "expected_land_goods"),
    [
        ("贸易-船货-直销-长协加价-B01", False),
        ("贸易-港口现货-市场加价-B07", False),
        ("贸易-港口现货-背对背-B06", False),
        ("贸易-代理落地-B09", True),
        ("贸易-落地-B05", True),
        ("贸易-船货-换月-已做套保-B05", False),
        ("贸易-船货-落地-已做套保-B07", True),
        ("贸易-代理落地-B0901", True),
        ("B0701", False),
        ("B0601", False),
        ("B0901", True),
        ("B0501", False),
        ("AB02", False),
    ],
)
def test_source_business_categories_are_preserved_and_classified_without_conversion_errors(source_category, expected_land_goods):
    from app.spot_ledger import normalize_sales_contract_record, record_to_public

    record = normalize_sales_contract_record({
        "detail_id": "D-SOURCE", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组",
        "contract_number": "C-SOURCE", "product_name": "铁矿石", "signed_date": "2026-08-25",
        "contract_quantity": "20", "business_category_code": source_category,
    })

    assert record["D"] == source_category
    assert record_to_public(record)["is_land_goods"] is expected_land_goods
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


def test_spot_ledger_is_the_first_sidebar_module_and_precedes_trading_management():
    from app import db

    spot_index = next(index for index, item in enumerate(db.MODULES) if item[1] == "spot_ledger")
    trading_index = next(index for index, item in enumerate(db.MODULES) if item[0] == "交易管理")

    assert spot_index == 0
    assert spot_index < trading_index


def test_supplier_legal_name_is_canonical_and_confirmed_alias_is_display_only():
    from app.spot_ledger import SUPPLIER_MAPPINGS, normalize_sales_contract_record, record_to_public

    source_supplier = next(iter(SUPPLIER_MAPPINGS))
    record = normalize_sales_contract_record({
        "detail_id": "D-SUPPLIER-CANONICAL", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组", "contract_number": "C-SUPPLIER-CANONICAL",
        "product_name": "PB粉", "product_category": "PB粉", "supplier": source_supplier,
        "signed_date": "2026-08-25", "contract_quantity": 20, "business_category_code": "B0701",
    })

    assert record["Q"] == source_supplier
    assert not any(error["field"] == "Q" for error in record["sync_errors"])
    public = record_to_public(record)
    assert public["supplier_display_name"] == SUPPLIER_MAPPINGS[source_supplier]


def test_unknown_nonempty_supplier_is_usable_without_mapping_anomaly():
    from app.spot_ledger import normalize_sales_contract_record, record_to_public

    source_supplier = "未配置映射但有法定全称的供应商有限公司"
    record = normalize_sales_contract_record({
        "detail_id": "D-SUPPLIER-UNKNOWN", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组", "contract_number": "C-SUPPLIER-UNKNOWN",
        "product_name": "PB粉", "product_category": "PB粉", "supplier": source_supplier,
        "signed_date": "2026-08-25", "contract_quantity": 20, "business_category_code": "B0701",
    })

    assert record["Q"] == source_supplier
    assert not any(error["field"] == "Q" for error in record["sync_errors"])
    assert record_to_public(record)["supplier_display_name"] == source_supplier


def test_empty_supplier_remains_a_true_sync_anomaly():
    from app.spot_ledger import normalize_sales_contract_record

    record = normalize_sales_contract_record({
        "detail_id": "D-SUPPLIER-MISSING", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组", "contract_number": "C-SUPPLIER-MISSING",
        "product_name": "PB粉", "product_category": "PB粉", "supplier": "",
        "signed_date": "2026-08-25", "contract_quantity": 20, "business_category_code": "B0701",
    })

    assert any(error["type"] == "missing_supplier" and error["field"] == "Q" for error in record["sync_errors"])


@pytest.mark.parametrize("source_category", [
    "SFGB粉", "IOH4粉", "南非钒钛块", "进口主焦煤", "PMC粉", "委内瑞拉精粉", "铁矿粉",
    "气煤", "主焦煤", "OH粉", "库兰粉", "进口钒钛球团矿", "昆巴粉",
])
def test_current_unknown_product_names_use_the_system_category_dictionary(source_category):
    from app.spot_ledger import normalize_sales_contract_record

    record = normalize_sales_contract_record({
        "detail_id": f"D-CATEGORY-{source_category}", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "大客户组", "profit_group": "大客户组", "contract_number": f"C-CATEGORY-{source_category}",
        "product_name": source_category, "product_category": source_category,
        "signed_date": "2026-08-25", "contract_quantity": 20, "business_category_code": "B0701",
    })

    assert record["AU"] == "非主流"
    assert not any(error["field"] == "AU" for error in record["sync_errors"])


def test_historical_records_are_publicly_out_of_scope_and_query_filters_have_2026_cutoff():
    from app.spot_ledger import _record_query_conditions, record_to_public

    historical = record_to_public({
        "record_id": "r-historical",
        "U": "2025-12-31", "sync_status": "异常", "supplement_status": "待补录",
        "sync_error_summary": '[{"field":"Q"}]',
    })
    current = record_to_public({
        "record_id": "r-current",
        "U": "2026-01-01", "sync_status": "异常", "supplement_status": "待补录",
        "sync_error_summary": '[{"field":"Q"}]',
    })
    assert historical["sync_status"] == "历史范围外"
    assert historical["supplement_status"] == "历史范围外"
    assert historical["sync_error_summary"] == []
    assert current["sync_status"] == "异常"
    conditions, values = _record_query_conditions({"sync_error": "true"})
    assert any('"U" >= ?' in condition for condition in conditions)
    assert "2026-01-01" in values
