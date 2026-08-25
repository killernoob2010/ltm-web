from __future__ import annotations

import base64
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
    monkeypatch.delenv("SPOT_LEDGER_SOURCE_USERNAME", raising=False)
    monkeypatch.delenv("SPOT_LEDGER_SOURCE_PASSWORD", raising=False)
    source = ProfiledSalesContractSource.from_env()
    with pytest.raises(SalesContractSourceError, match="auth_unavailable") as error:
        source.fetch_full_scan()
    assert error.value.stage == "auth_provider_missing"


def test_password_auth_uses_official_rsa_code_exchange_and_caches_bearer_token():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    from app.spot_ledger_sync import JianlongPasswordAuthProvider

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    public_key_expression = " +\n".join(repr(line) for line in public_pem.splitlines(keepends=True))
    login_html = f"""
        <form>
          <input name="redirectUri" value="https://tds.ejianlong.com/">
          <input name="appId" value="confirmed-app-id">
          <input name="companyId" value="">
        </form>
        <script>var publicKey = {public_key_expression};</script>
    """

    class Response:
        status_code = 200

        def __init__(self, *, text="", payload=None):
            self.text = text
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.calls = []
            self.headers = {}
            self.cookies = type("CookieJar", (), {"clear_calls": 0})()
            self.cookies.clear = lambda: setattr(
                self.cookies, "clear_calls", self.cookies.clear_calls + 1
            )

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            if kwargs.get("params", {}).get("code"):
                return Response(payload={"code": 200, "data": "bearer-token"})
            return Response(text=login_html)

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return Response(
                payload={
                    "code": 200,
                    "data": "https://tds.ejianlong.com/?code=one-time-code#/sign/saleContract",
                }
            )

    session = Session()
    provider = JianlongPasswordAuthProvider("employee-id", "personal-password", http=session)

    assert provider() == {"Authorization": "Bearer bearer-token"}
    assert provider() == {"Authorization": "Bearer bearer-token"}
    assert session.headers["User-Agent"] == "ltm-spot-ledger/1.0"
    assert len(session.calls) == 3
    password_payload = session.calls[1][2]["json"]["password"]
    decrypted = private_key.decrypt(base64.b64decode(password_payload), padding.PKCS1v15())
    assert decrypted == b"personal-password"
    assert session.calls[1][2]["json"]["username"] == "employee-id"
    assert session.calls[2][1] == "https://tds-api.ejianlong.com/login"
    assert session.calls[2][2]["params"] == {"code": "one-time-code"}
    assert session.calls[2][2]["headers"] == {
        "Origin": "https://tds.ejianlong.com",
        "Referer": "https://tds.ejianlong.com/",
    }
    assert "personal-password" not in repr(session.calls)
    assert session.cookies.clear_calls == 1
    assert provider.refresh() == {"Authorization": "Bearer bearer-token"}
    assert session.cookies.clear_calls == 2


def test_password_auth_errors_never_expose_credentials_or_source_response():
    from app.spot_ledger_sync import JianlongPasswordAuthProvider, SalesContractSourceError

    class Response:
        status_code = 503
        text = "personal-password bearer-token"

        def json(self):
            return {"code": 503, "msg": self.text}

    class Session:
        def get(self, _url, **_kwargs):
            return Response()

    provider = JianlongPasswordAuthProvider("employee-id", "personal-password", http=Session())

    with pytest.raises(SalesContractSourceError) as error:
        provider()
    assert "personal-password" not in str(error.value)
    assert "bearer-token" not in str(error.value)
    assert error.value.stage == "login_page_http"
    assert error.value.http_status == 503


def test_profiled_source_from_env_uses_one_session_for_login_and_report(monkeypatch):
    from app import spot_ledger_sync as sync

    session = object()
    monkeypatch.setenv("SPOT_LEDGER_SOURCE_USERNAME", "employee-id")
    monkeypatch.setenv("SPOT_LEDGER_SOURCE_PASSWORD", "personal-password")
    monkeypatch.delenv("SPOT_LEDGER_SOURCE_PROFILE", raising=False)
    monkeypatch.setattr(sync.requests, "Session", lambda: session)

    source = sync.ProfiledSalesContractSource.from_env()

    assert source.http is session
    assert isinstance(source.auth_provider, sync.JianlongPasswordAuthProvider)


def test_official_json_probe_uses_confirmed_post_and_returns_schema_only():
    from app import spot_ledger_sync as sync

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Http:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if "/tradeing/chain/saleList" in url:
                return Response({"code": 200, "data": []})
            if "/tdsSettle/queryJiesuan" in url:
                return Response(
                    {
                        "code": 200,
                        "data": {
                            "rows": [
                                {
                                    "settleObjectDetailId": "must-not-be-returned-settlement-id",
                                    "saleContractId": "must-not-be-returned-id",
                                    "saleContractMxId": "must-not-be-returned-sale-line-id",
                                    "countQuantity": 99,
                                }
                            ],
                            "total": 1,
                        },
                    }
                )
            return Response(
                {
                    "code": 200,
                    "data": {
                        "rows": [
                            {
                                "saleContractId": "must-not-be-returned-id",
                                "contractNo": "must-not-be-returned",
                                "goods": [{"saleContractMxId": "must-not-be-returned"}],
                            },
                            {"saleContractId": "must-not-be-returned-id-2"},
                        ],
                        "total": 1,
                    },
                }
            )

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if "/tradeing/chain/getById/" in url:
                chain_id = "must-not-be-returned-chain-id-2" if "chain-id-2" in url else "must-not-be-returned-chain-id"
                return Response(
                    {
                        "code": 200,
                        "data": {
                            "chainId": chain_id,
                            "profitAttribution": "must-not-be-returned-profit-group",
                            "quantityAttribution": "must-not-be-returned-quantity-group",
                        },
                    }
                )
            if "/tradeing/saleContract/must-not-be-returned-id-2" in url:
                return Response(
                    {
                        "code": 200,
                        "data": {
                            "chainId": "must-not-be-returned-chain-id-2",
                            "saleContractId": "must-not-be-returned-id-2",
                            "syncTradersId": "must-not-be-returned-traders-id",
                            "saleContractMxList": [],
                        },
                    }
                )
            if "/chain/goods/matchResult/" in url:
                return Response(
                    {
                        "code": 200,
                        "data": [
                            {
                                "saleId": "must-not-be-returned-resource-id",
                                "saleNo": "must-not-be-returned-resource-no",
                                "sourceDate": "must-not-be-returned-resource-date",
                            }
                        ],
                    }
                )
            if "/tradeing/sale?" in url:
                return Response(
                    {
                        "code": 200,
                        "data": {
                            "saleId": "must-not-be-returned-resource-id",
                            "sourceDate": "must-not-be-returned-resource-date",
                            "supplierName": "must-not-be-returned-resource-supplier",
                            "tdsGoodsList": [{"price": 66}],
                        },
                    }
                )
            if "/tradeing/purchaseContract/" in url:
                return Response(
                    {
                        "code": 200,
                        "data": {
                            "purchaseContractId": "must-not-be-returned-purchase-id",
                            "supplierName": "must-not-be-returned-supplier",
                            "purchaseContractMxList": [
                                {
                                    "purchaseContractMxId": "must-not-be-returned-purchase-line-id",
                                    "price": 88,
                                }
                            ],
                        },
                    }
                )
            if "/getRelevanceContract/" in url:
                return Response(
                    {
                        "code": 200,
                        "data": [
                            {
                                "purchaseContractId": "must-not-be-returned-purchase-id",
                                "purchaseContractMxId": "must-not-be-returned-purchase-line-id",
                                "supplierName": "must-not-be-returned-supplier",
                            }
                        ],
                    }
                )
            return Response(
                {
                    "code": 200,
                    "data": {
                        "chainId": "must-not-be-returned-chain-id",
                        "saleContractId": "must-not-be-returned-id",
                        "saleContractMxList": [
                            {
                                "saleContractMxId": "must-not-be-returned",
                                "contractQuantity": 100,
                            }
                        ],
                    },
                }
            )

    class Provider:
        def __call__(self):
            return {"Authorization": "Bearer bearer-token"}

    source = sync.ProfiledSalesContractSource(
        sync.build_candidate_source_profile("2026-08-25"),
        http=Http(),
        auth_provider=Provider(),
    )

    result = sync.probe_official_sales_contract_api(source=source)

    assert result == {
        "ok": True,
        "source_mode": "official_json",
        "http_status": 200,
        "response_code": "200",
        "detail_response_code": "200",
        "relevance_response_code": "200",
        "purchase_response_code": "200",
        "chain_response_code": "200",
        "resource_list_response_code": "200",
        "match_response_code": "200",
        "resource_detail_response_code": "200",
        "settlement_response_code": "200",
        "sampled_contract_count": 2,
        "schema_paths": [
            "code",
            "data",
            "data.rows",
            "data.rows[]",
            "data.rows[].contractNo",
            "data.rows[].goods",
            "data.rows[].goods[]",
            "data.rows[].goods[].saleContractMxId",
            "data.rows[].saleContractId",
            "data.total",
        ],
        "detail_schema_paths": [
            "code",
            "data",
            "data.chainId",
            "data.saleContractId",
            "data.saleContractMxList",
            "data.saleContractMxList[]",
            "data.saleContractMxList[].contractQuantity",
            "data.saleContractMxList[].saleContractMxId",
            "data.syncTradersId",
        ],
        "relevance_schema_paths": [
            "code",
            "data",
            "data[]",
            "data[].purchaseContractId",
            "data[].purchaseContractMxId",
            "data[].supplierName",
        ],
        "purchase_schema_paths": [
            "code",
            "data",
            "data.purchaseContractId",
            "data.purchaseContractMxList",
            "data.purchaseContractMxList[]",
            "data.purchaseContractMxList[].price",
            "data.purchaseContractMxList[].purchaseContractMxId",
            "data.supplierName",
        ],
        "chain_schema_paths": [
            "code",
            "data",
            "data.chainId",
            "data.profitAttribution",
            "data.quantityAttribution",
        ],
        "resource_list_schema_paths": [
            "code",
            "data",
            "data[]",
        ],
        "match_schema_paths": [
            "code",
            "data",
            "data[]",
            "data[].saleId",
            "data[].saleNo",
            "data[].sourceDate",
        ],
        "resource_detail_schema_paths": [
            "code",
            "data",
            "data.saleId",
            "data.sourceDate",
            "data.supplierName",
            "data.tdsGoodsList",
            "data.tdsGoodsList[]",
            "data.tdsGoodsList[].price",
        ],
        "settlement_schema_paths": [
            "code",
            "data",
            "data.rows",
            "data.rows[]",
            "data.rows[].countQuantity",
            "data.rows[].saleContractId",
            "data.rows[].saleContractMxId",
            "data.rows[].settleObjectDetailId",
            "data.total",
        ],
    }
    assert source.http.calls == [
        (
            "https://tds-api.ejianlong.com/tradeing/saleContract/saleContractList",
            {
                "params": {"sheetCode": "G01009", "pageNum": 1, "pageSize": 10},
                "json": {"status": "70", "isQryAll": "N"},
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/saleContract/must-not-be-returned-id?sheetCode=G01009",
            {
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/saleContract/getRelevanceContract/must-not-be-returned-id?sheetCode=G01009",
            {
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/purchaseContract/must-not-be-returned-purchase-id?sheetCode=G01008",
            {
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/chain/getById/must-not-be-returned-chain-id?sheetCode=G01004",
            {
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/chain/saleList?sheetCode=G01004",
            {
                "json": {"chainId": "must-not-be-returned-chain-id", "tradersId": ""},
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/saleContract/must-not-be-returned-id-2?sheetCode=G01009",
            {
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/chain/getById/must-not-be-returned-chain-id-2?sheetCode=G01004",
            {
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/chain/saleList?sheetCode=G01004",
            {
                "json": {"chainId": "must-not-be-returned-chain-id-2", "tradersId": ""},
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/chain/goods/matchResult/must-not-be-returned-traders-id?sheetCode=G01004",
            {
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tradeing/sale?sheetCode=G01003",
            {
                "params": {"saleId": "must-not-be-returned-resource-id"},
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
        (
            "https://tds-api.ejianlong.com/tdsSettle/queryJiesuan?sheetCode=G01112",
            {
                "params": {"pageNum": 1, "pageSize": 10},
                "json": {"status": "70"},
                "headers": {
                    "Authorization": "Bearer bearer-token",
                    "Origin": "https://tds.ejianlong.com",
                    "Referer": "https://tds.ejianlong.com/",
                },
                "timeout": 30,
            },
        ),
    ]
    assert "must-not-be-returned" not in repr(result)


@pytest.mark.parametrize(
    ("expired_status", "expired_url"),
    [
        (401, "https://tds-report.ejianlong.com/jmreport/show"),
        (200, "https://server-auth.ejianlong.com/login?appId=confirmed-app-id"),
    ],
)
def test_profiled_source_reauthenticates_once_after_bearer_expiry(expired_status, expired_url):
    from app.spot_ledger_sync import ProfiledSalesContractSource, build_candidate_source_profile

    class Response:
        def __init__(self, status_code, payload=None, *, url="https://tds-report.ejianlong.com/jmreport/show"):
            self.status_code = status_code
            self.payload = payload or {}
            self.url = url

        def json(self):
            return self.payload

    class Http:
        def __init__(self):
            self.calls = []

        def post(self, _url, **kwargs):
            self.calls.append(kwargs["headers"])
            if len(self.calls) == 1:
                return Response(expired_status, url=expired_url)
            row = {
                "销售合同商品明细id": "DETAIL-1",
                "期现货": "现货",
                "合同状态": "生效",
                "量归属组": "大客户组",
                "业务毛利归属组": "大客户组",
                "业务类别": "B07",
                "公司": "操作抬头",
                "资源日期": "2026-08-20",
                "物资名称": "铁矿石",
                "合同卸货港": "曹妃甸港",
                "定价模式": "固定价",
                "中文船名": "",
                "合同数量": 100,
                "结算数量": None,
                "结案状态": "未结案",
                "资源单单价": 700,
                "资源方": "供应商",
                "资源业务员": "采购员",
                "初始资源单创建人": "执行员",
                "签订日期": "2026-08-21",
                "合同单价": 750,
                "需求方": "客户",
                "销售合同号": "XS-1",
                "需求业务员": "销售员",
                "合同创建人": "销售执行员",
            }
            return Response(
                200,
                {
                    "code": 200,
                    "result": {
                        "dataList": {
                            "TJJLYSHZ": {"list": [row], "count": 1, "total": 1}
                        }
                    },
                },
            )

    class Provider:
        def __init__(self):
            self.refresh_count = 0

        def __call__(self):
            return {"Authorization": "Bearer old-token"}

        def refresh(self):
            self.refresh_count += 1
            return {"Authorization": "Bearer new-token"}

    http = Http()
    provider = Provider()
    source = ProfiledSalesContractSource(
        build_candidate_source_profile("2026-08-25", page_size=1),
        http=http,
        auth_provider=provider,
    )

    scan = source.fetch_full_scan()

    assert scan.complete is True
    assert provider.refresh_count == 1
    assert http.calls == [
        {"Authorization": "Bearer old-token"},
        {"Authorization": "Bearer new-token"},
    ]


def test_scheduler_startup_uses_only_latest_due_hour():
    from app.spot_ledger_sync import scheduler_due_slots

    now = datetime.fromisoformat("2026-08-24T18:15:42+08:00")
    attempted = set()
    latest = scheduler_due_slots(now, attempted, startup=True)
    assert latest == ["2026-08-24T18:00+08:00"]
    assert len(attempted) == 9
    attempted.update(latest)
    assert scheduler_due_slots(now, attempted, startup=False) == []
    assert len(scheduler_due_slots(now, set(), startup=False)) == 10


def test_scheduler_startup_after_sync_window_does_not_run_catch_up():
    from app.spot_ledger_sync import scheduler_due_slots

    attempted = set()
    assert scheduler_due_slots(
        datetime.fromisoformat("2026-08-24T19:00:00+08:00"),
        attempted,
        startup=True,
    ) == []
    assert len(attempted) == 10


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
