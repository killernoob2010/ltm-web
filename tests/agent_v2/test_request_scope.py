from datetime import datetime, timezone
import pytest
from app.trading_agent import request_scope
from app.trading_agent.answer_contracts import ValidatedAnswer21


def test_relative_month_uses_shanghai_year_not_model_memory():
    scope = request_scope.resolve('日照港，今年8月份每周库存变化', datetime(2025, 12, 31, 16, tzinfo=timezone.utc))
    assert scope == {'start_date': '2026-08-01', 'end_date': '2026-08-31', 'ports': ['日照']}
    assert request_scope.resolve('去年2月库存', datetime(2025, 9, 11, tzinfo=timezone.utc))['end_date'] == '2024-02-29'
    assert request_scope.resolve('对比今年8月和去年8月库存', datetime(2026, 9, 11, tzinfo=timezone.utc)) == {}


def test_wrong_month_or_port_query_is_rejected_before_execution():
    scope = {'start_date': '2026-08-01', 'end_date': '2026-08-31', 'ports': ['日照']}
    args = {'dataset': 'port_inventory', 'mode': 'range', 'start_date': '2025-08-01', 'end_date': '2025-08-31', 'filters': {'ports': ['日照']}}
    with pytest.raises(ValueError, match='request_scope_mismatch'):
        request_scope.check_query(scope, args)
    args.update(start_date='2026-08-01', end_date='2026-08-31')
    request_scope.check_query(scope, args)
    args['filters']['ports'] = ['青岛']
    with pytest.raises(ValueError, match='request_scope_mismatch'):
        request_scope.check_query(scope, args)


def test_failed_inventory_lookup_cannot_deliver_complete_knowledge_answer():
    scope = {'start_date': '2026-08-01', 'end_date': '2026-08-31', 'ports': ['日照']}
    answer = ValidatedAnswer21(delivery_status='complete', body_markdown='2025年8月无法确认', plain_text='2025年8月无法确认')
    result = request_scope.assess(answer, scope, [], ['query_dataset'])
    assert result.delivery_status == 'partial'
    assert '2025' not in result.plain_text
    assert '2026-08-01' in result.plain_text
    assert result.limitations[0].code == 'request_scope_unverified'
