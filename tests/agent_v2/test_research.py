from app.trading_agent.research import QueryRejected, UnsafeSource, validate_public_query


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


def test_unsafe_sources_reject_private_addresses(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET,0,0,"",("127.0.0.1",80))])
    import pytest
    from app.trading_agent.research import _validate_url
    with pytest.raises(UnsafeSource):
        _validate_url("http://example.com/private")
