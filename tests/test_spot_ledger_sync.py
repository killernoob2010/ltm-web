from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "spot_ledger_sales_contract_fixture.json"


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    from app import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "spot-ledger.db")
    db.init_db()
    return db


def load_fixture_scan():
    from app.spot_ledger_sync import FixtureSalesContractSource

    return FixtureSalesContractSource(FIXTURE_PATH).fetch_full_scan()


def test_fixture_scan_accepts_only_seven_groups_spot_and_effective_records():
    from app.spot_ledger import SHANGHAI_GROUPS

    scan = load_fixture_scan()
    assert set(record["E"] for record in scan.records) == set(SHANGHAI_GROUPS)
    assert all(record["eligible"] for record in scan.records)
    assert len({record["source_detail_id"] for record in scan.records}) == len(scan.records)
    assert sum(record["AD"] == "C-100" for record in scan.records) == 2
    assert {record["D"] for record in scan.records} >= {"现货-市场加价", "现货-背对背", "船货-落地"}


def test_full_scan_upsert_is_idempotent_and_preserves_manual_fields(ledger_db):
    from app.spot_ledger_sync import apply_full_scan

    scan = load_fixture_scan()
    first = apply_full_scan(scan, "2026-08-24T09:00+08:00")
    second = apply_full_scan(scan, "2026-08-24T10:00+08:00")
    assert first["inserted"] == len(scan.records)
    assert second["inserted"] == 0
    assert second["updated"] == len(scan.records)
    with ledger_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM spot_ledger_records WHERE record_source_type = '现货同步'").fetchone()["c"]
        detail = conn.execute("SELECT source_detail_id FROM spot_ledger_records WHERE \"AD\" = 'C-100' ORDER BY source_detail_id").fetchall()
    assert count == len(scan.records)
    assert len(detail) == 2


def test_incomplete_scan_does_not_hide_existing_record(ledger_db):
    from app.spot_ledger_sync import FullScanResult, apply_full_scan, get_active_records

    first = load_fixture_scan()
    apply_full_scan(first, "2026-08-24T09:00+08:00")
    broken = FullScanResult(
        records=first.records[:1], page_count=1, expected_page_count=2,
        total_count=1, complete=False, errors=["page_missing"], source_mode="fixture",
    )
    result = apply_full_scan(broken, "2026-08-24T10:00+08:00")
    assert result["hidden"] == 0
    assert len(get_active_records()) == len(first.records)


def test_complete_scan_soft_hides_missing_record_only_after_success(ledger_db):
    from app.spot_ledger_sync import FullScanResult, apply_full_scan, get_active_records

    first = load_fixture_scan()
    apply_full_scan(first, "2026-08-24T09:00+08:00")
    smaller = FullScanResult(
        records=first.records[:1], page_count=1, expected_page_count=1,
        total_count=1, complete=True, errors=[], source_mode="fixture",
    )
    result = apply_full_scan(smaller, "2026-08-24T10:00+08:00")
    assert result["hidden"] == len(first.records) - 1
    assert len(get_active_records()) == 1


def test_duplicate_detail_ids_make_scan_incomplete():
    from app.spot_ledger_sync import FullScanResult, validate_full_scan

    scan = load_fixture_scan()
    duplicate = FullScanResult(
        records=[scan.records[0], scan.records[0]], page_count=1,
        expected_page_count=1, total_count=2, complete=True, errors=[], source_mode="fixture",
    )
    result = validate_full_scan(duplicate)
    assert not result.complete
    assert any("duplicate_detail_id" in str(error) for error in result.errors)


def test_unattended_source_without_profile_is_explicitly_blocked(monkeypatch):
    from app.spot_ledger_sync import ProfiledSalesContractSource, SalesContractSourceError

    monkeypatch.delenv("SPOT_LEDGER_SOURCE_PROFILE", raising=False)
    source = ProfiledSalesContractSource.from_env()
    with pytest.raises(SalesContractSourceError, match="auth_unavailable"):
        source.fetch_full_scan()


def test_confirmed_candidate_request_body_matches_observed_contract():
    from app.spot_ledger_sync import build_candidate_request_body

    body = build_candidate_request_body("2026-08-24", page_no=1, page_size=20)
    assert body == {
        "id": "1055351755192311808",
        "apiUrl": "",
        "params": json.dumps(
            {
                "pageNo": 1,
                "periodDate": "2026-08-24",
                "releaseDate": "2026-08-24",
                "TJJLYSHZ__期现货": "现货",
                "TJJLYSHZ__合同状态": "生效",
                "TJJLYSHZ__业务毛利归属组": "大客户组,东北组,山东组,黄骅组,天津组,唐山组,南方组",
                "pageSize": "20",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def test_profiled_source_paginates_confirmed_json_request_without_guessing_response_shape():
    from app.spot_ledger_sync import CANDIDATE_SOURCE_URL, ProfiledSalesContractSource, build_candidate_request_body

    class Response:
        status_code = 200

        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return {"result": {"rows": self.rows, "total": 2, "pageCount": 2}}

    class Http:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            request_body = kwargs["json"]
            self.calls.append({"url": url, "json": request_body, "headers": kwargs["headers"], "timeout": kwargs["timeout"]})
            params = json.loads(request_body["params"])
            row = {
                "detail_id": f"D{params['pageNo']}",
                "spot_type": "现货",
                "contract_status": "生效",
                "quantity_group": "大客户组",
                "profit_group": "大客户组",
                "contract_number": f"C-{params['pageNo']}",
                "product_name": "铁矿石",
                "signed_date": "2026-08-20",
                "resource_date": "2026-08-15",
                "contract_quantity": 100,
                "business_category_code": "B07",
            }
            return Response([row])

    http = Http()
    profile = {
        "url": CANDIDATE_SOURCE_URL,
        "request_body": build_candidate_request_body("2026-08-24", page_no=1, page_size=1),
        "records_path": "result.rows",
        "total_path": "result.total",
        "page_count_path": "result.pageCount",
        "field_map": {
            "detail_id": "detail_id",
            "spot_type": "spot_type",
            "contract_status": "contract_status",
            "quantity_group": "quantity_group",
            "profit_group": "profit_group",
            "contract_number": "contract_number",
            "product_name": "product_name",
            "signed_date": "signed_date",
            "resource_date": "resource_date",
            "contract_quantity": "contract_quantity",
            "business_category_code": "business_category_code",
        },
        "pagination": {
            "params_key": "params",
            "page_number_key": "pageNo",
            "page_size_key": "pageSize",
        },
    }
    source = ProfiledSalesContractSource(profile, http=http, auth_provider=lambda: {"X-Test": "fixture"})

    scan = source.fetch_full_scan()

    assert scan.complete is True
    assert scan.source_mode == "profiled_http"
    assert scan.total_count == 2
    assert [record["source_detail_id"] for record in scan.records] == ["D1", "D2"]
    assert [json.loads(call["json"]["params"])["pageNo"] for call in http.calls] == [1, 2]
    assert all(call["url"] == CANDIDATE_SOURCE_URL for call in http.calls)


def test_confirmed_profile_reads_observed_jmreport_shape_and_maps_system_fields():
    from app.spot_ledger_sync import ProfiledSalesContractSource, build_candidate_source_profile

    class Response:
        status_code = 200

        def __init__(self, page_no):
            self.page_no = page_no

        def json(self):
            row = {
                "销售合同商品明细id": f"REAL-{self.page_no}",
                "期现货": "现货",
                "合同状态": "生效",
                "量归属组": "山东组",
                "业务毛利归属组": "唐山组",
                "业务类别": "贸易-代理落地-B09",
                "公司": "操作抬头A",
                "资源日期": "2026-08-20 00:00:00",
                "物资名称": "铁矿石",
                "合同卸货港": "日照港",
                "定价模式": "固定价",
                "中文船名": "测试船",
                "合同数量": "120.0000",
                "结算数量": "100.0000",
                "结案状态": "已结案",
                "资源单单价": "770.50",
                "资源方": "供应商A",
                "资源业务员": "采购业务员",
                "初始资源单创建人": "采购执行员",
                "签订日期": "2026-08-21 00:00:00",
                "合同单价": "820.25",
                "需求方": "签约客户",
                "销售合同号": f"XS-{self.page_no}",
                "需求业务员": "销售业务员",
                "合同创建人": "920109_销售执行员",
            }
            return {
                "success": True,
                "message": "",
                "code": 200,
                "result": {
                    "id": "1055351755192311808",
                    "dataList": {
                        "expData": {},
                        "replaceParams": {"pageNo": self.page_no, "pageSize": 1},
                        "TJJLYSHZ": {
                            "total": 2,
                            "count": 2,
                            "isPage": "1",
                            "isList": "1",
                            "dbType": "mysql",
                            "list": [row],
                            "linkList": None,
                        },
                    },
                },
                "timestamp": 1787587200000,
            }

    class Http:
        def post(self, _url, **kwargs):
            page_no = json.loads(kwargs["json"]["params"])["pageNo"]
            return Response(page_no)

    profile = build_candidate_source_profile("2026-08-25", page_size=1)
    source = ProfiledSalesContractSource(profile, http=Http(), auth_provider=lambda: {"X-Test-Auth": "fixture"})

    scan = source.fetch_full_scan()

    assert scan.complete is True
    assert scan.total_count == 2
    assert scan.page_count == 2
    assert [record["source_detail_id"] for record in scan.records] == ["REAL-1", "REAL-2"]
    first = scan.records[0]
    assert first["D"] == "船货-落地"
    assert first["E"] == "山东组" and first["AP"] == "唐山组" and first["AQ"] == "是"
    assert first["F"] == "操作抬头A"
    assert first["G"] == "2026-08-20" and first["U"] == "2026-08-21"
    assert first["H"] == "铁矿石" and first["I"] == "日照港" and first["J"] == "固定价"
    assert first["K"] == "测试船" and first["L"] == first["X"] == 100
    assert first["M"] == 770.5 and first["Z"] == 820.25
    assert first["Q"] == "供应商A" and first["S"] == "采购业务员" and first["T"] == "采购执行员"
    assert first["AB"] == "签约客户" and first["AD"] == "XS-1"
    assert first["AF"] == "销售业务员" and first["AG"] == "销售执行员"
    assert first["source_closed_state"] == "已结案"


def test_due_slots_are_daily_nine_to_eighteen_and_second_free():
    from app.spot_ledger_sync import due_spot_ledger_slots

    slots = due_spot_ledger_slots(datetime.fromisoformat("2026-08-24T18:15:42+08:00"))
    assert len(slots) == 10
    assert slots[0].endswith("09:00+08:00")
    assert slots[-1].endswith("18:00+08:00")
    assert all("." not in slot and ":42" not in slot for slot in slots)


def test_history_migration_only_updates_unique_match_and_defaults_to_dry_run(ledger_db, tmp_path):
    from openpyxl import Workbook, load_workbook
    from app.spot_ledger_sync import apply_full_scan, migrate_history_workbook

    scan = load_fixture_scan()
    apply_full_scan(scan, "2026-08-24T09:00+08:00")
    path = tmp_path / "history.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "现货业务台账"
    sheet.append(["销售合同号", "商品名称", "销售价格（元/吨）", "销售数量（吨）", "备注"])
    sheet.append(["C-100", "铁矿石", 800, 100, "历史人工备注"])
    sheet.append(["不存在", "铁矿石", 800, 100, "不应写入"])
    workbook.save(path)
    preview = migrate_history_workbook(path)
    assert preview["matched"] == 1
    assert preview["updated"] == 0
    applied = migrate_history_workbook(path, apply=True)
    assert applied["updated"] == 1
    with ledger_db.connect() as conn:
        row = conn.execute("SELECT \"AM\" FROM spot_ledger_records WHERE \"AD\" = 'C-100' ORDER BY source_detail_id LIMIT 1").fetchone()
    assert row["AM"] == "历史人工备注"
