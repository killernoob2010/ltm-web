import pytest

from app.trading_agent import research, store
from app.trading_agent.contracts import ToolEnvelope
from app.trading_agent.research import QueryRejected, UnsafeSource, validate_public_query
from test_store import queued


@pytest.mark.parametrize("freshness", ["none", "day", "week", "month", "year"])
def test_tavily_basic_search_preserves_sources_and_budget(queued, monkeypatch, freshness):
    from urllib.parse import urlparse
    from app.trading_agent.research_policy import public_provider_readiness

    principal = store.principal_for_task(store.claim_next("tavily-worker"))
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-tavily")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "synthetic-brave")
    monkeypatch.setattr(research, "_validate_url", urlparse)

    class Session:
        def post(self, url, **kwargs):
            assert url == "https://api.tavily.com/search"
            assert kwargs["headers"]["Authorization"] == "Bearer synthetic-tavily"
            args = kwargs["json"]
            assert args["query"] == "铁矿石供需"
            assert args["search_depth"] == "basic"
            assert args["auto_parameters"] is False
            assert args["include_answer"] is False
            assert args["include_raw_content"] is False
            assert args.get("time_range") == (None if freshness == "none" else freshness)
            assert kwargs["allow_redirects"] is False
            return type("Response", (), {"status_code": 200, "json": lambda _: {
                "results": [{"title": "供需", "url": "https://example.com/report",
                             "content": "公开资料摘要", "published_date": "2026-09-10"}],
            }})()

    assert public_provider_readiness() == {"provider": "tavily", "status": "available"}
    result = research.search_public(principal, "铁矿石供需", freshness=freshness, session=Session())
    assert result.status == "complete"
    assert result.payload["provider"] == "tavily"
    source = result.payload["sources"][0]
    assert source["description"] == "公开资料摘要"
    assert source["published_label"] == "2026-09-10"
    assert source["source_ref"]


@pytest.mark.parametrize("status", [302, 401, 429, 432, 433, 500])
def test_tavily_failure_does_not_fall_back_or_leak_response(queued, monkeypatch, status):
    principal = store.principal_for_task(store.claim_next("tavily-failure-worker"))
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-tavily")

    class Session:
        def post(self, *args, **kwargs):
            return type("Response", (), {"status_code": status})()

    result = research.search_public(principal, "铁矿石供需", session=Session())
    assert result.status == "temporarily_unavailable"
    assert result.payload["provider"] == "tavily"
    assert "synthetic-tavily" not in str(result)


def test_public_query_rejects_private_amount_and_internal_context():
    import pytest
    for query in ("本账户亏损987654.32怎样评价", "近期天气如何影响订单ABC123456", "我的持仓风险如何"):
        with pytest.raises(QueryRejected):
            validate_public_query(query, private_context=["987654.32", "ABC123456"])


def test_public_query_allows_general_method_and_date():
    query = validate_public_query("2026年铁矿石期权 Black76 风险分析方法")
    assert query.approved is True


def test_public_query_allows_generic_position_method_without_private_context():
    query = validate_public_query("期权持仓 Greeks 风险评估方法")
    assert query.approved is True


def test_date_does_not_bypass_private_value():
    with pytest.raises(QueryRejected):
        validate_public_query("2026年9月 铁矿石 987654.32", private_context=["987654.32"])


def test_private_context_matching_normalizes_unicode_entities_and_numeric_tokens():
    with pytest.raises(QueryRejected):
        validate_public_query("ABC&#49;23&#52;56", private_context=["ABC123456"])
    with pytest.raises(QueryRejected):
        validate_public_query("abc-123 456", private_context=["ABC123456"])
    with pytest.raises(QueryRejected):
        validate_public_query("宏源\u200b测试账户", private_context=["宏源测试账户"])
    with pytest.raises(QueryRejected):
        validate_public_query("２０２６年９月 铁矿石 987,654.320", private_context=["987654.32"])
    assert validate_public_query("2026年9月 铁矿石", private_context=["2", "2026-09-10"]).approved is True


def test_search_maps_freshness_and_keeps_age_as_label(queued, monkeypatch):
    from urllib.parse import urlparse

    principal = store.principal_for_task(store.claim_next("research-freshness-worker"))
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "synthetic")
    monkeypatch.setattr(research, "_validate_url", urlparse)

    class FreshResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"web": {"results": [{
                "title": "Method", "url": "https://example.com/method",
                "description": "public", "age": "2 days ago",
            }]}}

    class FreshSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FreshResponse()

    session = FreshSession()
    envelope = research.search_public(principal, "铁矿石风险管理方法", freshness="week", session=session)

    assert session.calls[0][1]["params"]["freshness"] == "pw"
    source = envelope.payload["sources"][0]
    assert source["published_at"] is None
    assert source["published_label"] == "2 days ago"


def test_missing_search_key_does_not_call_session(queued, monkeypatch):
    principal = store.principal_for_task(store.claim_next("research-no-key-worker"))
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    class Session:
        def __init__(self):
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("search provider must not be called without a key")

    session = Session()
    result = research.search_public(principal, "铁矿石风险管理方法", session=session)

    assert result.status == "temporarily_unavailable"
    assert session.calls == 0


def test_empty_search_results_are_partial_and_not_a_public_success(queued, monkeypatch):
    principal = store.principal_for_task(store.claim_next("research-empty-worker"))
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "synthetic")

    class EmptyResponse:
        status_code = 200

        def json(self):
            return {"web": {"results": []}}

    class EmptySession:
        def get(self, *args, **kwargs):
            return EmptyResponse()

    result = research.search_public(principal, "铁矿石供需", session=EmptySession())

    assert result.status == "partial"
    assert result.payload["search_status"] == "no_results"
    assert result.payload["source_count"] == 0
    assert any("没有返回" in warning for warning in result.warnings)


def test_private_context_reads_only_current_authorized_result_values(queued):
    principal = store.principal_for_task(store.claim_next("research-context-worker"))
    store.save_result(
        principal,
        ToolEnvelope(
            status="complete",
            captured_at=store.now(),
            calculation_version="context-test",
            payload={"kind": "positions"},
        ),
        [{
            "account_label": "宏源测试账户",
            "contract": "I2609-P-650",
            "quantity": "2",
            "average_price": "987654.32",
            "floating_pnl": "-12345.67",
        }],
    )

    context = research.build_private_context(principal)

    assert "宏源测试账户" in context
    assert "I2609-P-650" in context
    assert "987654.32" in context


def test_unsafe_sources_reject_private_addresses(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET,0,0,"",("127.0.0.1",80))])
    import pytest
    from app.trading_agent.research import _validate_url
    with pytest.raises(UnsafeSource):
        _validate_url("http://example.com/private")
