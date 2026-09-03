"""Contract tests for the bounded closing review model gateway."""

import json
import os
import sys

import pytest
import requests
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.closing_review_model_gateway import (  # noqa: E402
    DeepSeekClosingReviewProvider,
    FakeClosingReviewProvider,
    IntentRequest,
    IntentResolution,
    ModelGatewayError,
)


def _resolution(**overrides):
    values = {
        "task_profile": "option_realized_pnl_query",
        "date_expression": "20260831",
        "reference_mode": "explicit_date",
        "needs_clarification": False,
        "clarification_question": None,
    }
    values.update(overrides)
    return IntentResolution(**values)


def _request(**overrides):
    values = {
        "request_id": "request-1",
        "user_text": "这一天实际平仓盈亏是多少？",
        "context_messages": [
            {
                "role": "user",
                "message_type": "user",
                "content": "这一天实际平仓盈亏是多少？",
                "created_at": "2026-09-03T12:00:00+08:00",
            }
        ],
        "anchor": {
            "task_profile": "option_realized_pnl_query",
            "target_date": "20260831",
            "displayed_metric_labels": ["真实平仓盈亏"],
            "result_ref": "task:previous",
        },
        "conversation_id": 999,
    }
    values.update(overrides)
    return IntentRequest(**values)


def _payload(content):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 41, "completion_tokens": 9, "total_tokens": 50},
    }


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = "provider response body"

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def monotonic(self):
        self.value += 0.01
        return self.value


def test_intent_schema_rejects_extra_fields_and_validates_clarification_contract():
    with pytest.raises(ValidationError):
        _resolution(unexpected="must not pass")

    with pytest.raises(ValidationError):
        _resolution(needs_clarification=True, clarification_question=None)

    with pytest.raises(ValidationError):
        _resolution(needs_clarification=False, clarification_question="多余问题")

    clarified = _resolution(
        task_profile="unsupported",
        date_expression=None,
        reference_mode="none",
        needs_clarification=True,
        clarification_question="请明确要查询哪一个交易日？",
    )
    assert clarified.clarification_question


def test_fake_provider_minimizes_and_bounds_context_before_recording_request():
    messages = [
        {
            "role": "user",
            "message_type": "user",
            "content": f"第 {index} 轮",
            "created_at": "2026-09-03T12:00:00+08:00",
            "conversation_id": 999,
            "statement_text": "do not forward",
        }
        for index in range(20)
    ]
    messages.append(
        {
            "role": "user",
            "message_type": "user",
            "content": "来自另一个 conversation 的内容",
            "conversation_id": 123,
        }
    )
    request = _request(
        user_text="请只回答当前问题",
        context_messages=messages,
        anchor={
            "task_profile": "option_realized_pnl_query",
            "target_date": "20260831",
            "realized_close_pnl": 123456.78,
            "database_url": "postgresql://secret",
        },
    )
    provider = FakeClosingReviewProvider(_resolution())

    result = provider.resolve_intent(request)

    assert result.task_profile == "option_realized_pnl_query"
    safe_request = provider.last_request.model_dump(mode="json")
    assert len(safe_request["context_messages"]) <= 12
    serialized = json.dumps(safe_request, ensure_ascii=False)
    for forbidden in (
        "statement_text",
        "database_url",
        "account_number",
        "raw_rows",
        "api_key",
        "123456.78",
        "另一个 conversation",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "first_event",
    [
        requests.exceptions.ConnectTimeout("connect timeout"),
        requests.exceptions.ReadTimeout("read timeout"),
        FakeResponse(429, {"error": {"message": "rate limited"}}),
        FakeResponse(500, {"error": {"message": "server error"}}),
    ],
)
def test_retryable_provider_failures_have_one_retry_then_can_succeed(first_event):
    session = FakeSession([first_event, FakeResponse(200, _payload(json.dumps(_resolution().model_dump())))])
    provider = DeepSeekClosingReviewProvider(
        api_key="test-key",
        api_base="https://api.deepseek.com",
        model="deepseek-chat",
        session=session,
        clock=FakeClock(),
    )

    result = provider.resolve_intent(_request())

    assert result.task_profile == "option_realized_pnl_query"
    assert len(session.calls) == 2
    assert session.calls[0][0] == "https://api.deepseek.com/chat/completions"
    assert session.calls[0][1]["timeout"] == 15
    assert provider.last_metadata["provider"] == "deepseek"
    assert provider.last_metadata["model"] == "deepseek-chat"
    assert provider.last_metadata["usage"]["total_tokens"] == 50
    assert provider.last_metadata["finish_reason"] == "stop"


def test_non_retryable_4xx_is_not_retried_and_does_not_expose_payload():
    session = FakeSession([FakeResponse(400, {"error": {"message": "bad request"}})])
    provider = DeepSeekClosingReviewProvider(
        api_key="super-secret-key",
        session=session,
        clock=FakeClock(),
    )

    with pytest.raises(ModelGatewayError) as caught:
        provider.resolve_intent(_request(user_text="包含用户敏感内容"))

    assert caught.value.category == "provider_4xx"
    assert caught.value.retryable is False
    assert len(session.calls) == 1
    assert "super-secret-key" not in str(caught.value)
    assert "包含用户敏感内容" not in str(caught.value)


def test_two_timeouts_fail_closed_with_bounded_two_call_budget():
    session = FakeSession([
        requests.exceptions.ReadTimeout("first"),
        requests.exceptions.ReadTimeout("second"),
    ])
    provider = DeepSeekClosingReviewProvider(
        api_key="test-key",
        session=session,
        clock=FakeClock(),
    )

    with pytest.raises(ModelGatewayError) as caught:
        provider.resolve_intent(_request())

    assert caught.value.category == "temporarily_unavailable"
    assert len(session.calls) == 2
    assert provider.last_metadata["attempt_count"] == 2


def test_invalid_json_or_schema_gets_one_same_budget_repair_attempt():
    invalid = _payload("not json")
    valid = _payload(json.dumps(_resolution().model_dump()))
    session = FakeSession([FakeResponse(200, invalid), FakeResponse(200, valid)])
    provider = DeepSeekClosingReviewProvider(api_key="test-key", session=session, clock=FakeClock())

    result = provider.resolve_intent(_request())

    assert result.reference_mode == "explicit_date"
    assert len(session.calls) == 2
    first_messages = session.calls[0][1]["json"]["messages"]
    second_messages = session.calls[1][1]["json"]["messages"]
    assert first_messages[0]["role"] == "system"
    assert second_messages[-1]["role"] == "user"
    assert "JSON" in second_messages[-1]["content"]


def test_missing_key_fails_before_http_and_provider_metadata_is_safe():
    session = FakeSession([])
    provider = DeepSeekClosingReviewProvider(api_key="", session=session)

    with pytest.raises(ModelGatewayError) as caught:
        provider.resolve_intent(_request())

    assert caught.value.category == "missing_configuration"
    assert caught.value.retryable is False
    assert session.calls == []
    assert provider.last_metadata["provider"] == "deepseek"
    assert "prompt" not in provider.last_metadata
    assert "api_key" not in provider.last_metadata
