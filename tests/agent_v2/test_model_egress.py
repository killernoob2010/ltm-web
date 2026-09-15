import pytest

from app.trading_agent.egress_policy import (
    EgressDenied,
    redact_trace_metadata,
    redact_exception,
    sanitize_model_payload,
    validate_model_destination,
)


def test_private_value_in_allowed_field_is_rejected():
    with pytest.raises(EgressDenied) as error:
        sanitize_model_payload(
            {"summary": "客户 INTERNAL-CUSTOMER-CANARY"},
            allowed_paths={"summary"},
            sensitive_values={"INTERNAL-CUSTOMER-CANARY"},
        )
    assert error.value.code == "private_content"


def test_sensitive_value_is_rejected_even_when_the_field_would_be_hidden():
    with pytest.raises(EgressDenied) as error:
        sanitize_model_payload(
            {"internal": {"customer": "INTERNAL-CUSTOMER-CANARY"}},
            allowed_paths={"summary"},
            sensitive_values={"INTERNAL-CUSTOMER-CANARY"},
        )
    assert error.value.code == "private_content"


def test_contract_identity_field_cannot_be_opted_into():
    result = sanitize_model_payload(
        {"contract": "SHFE-PRIVATE-CONTRACT"},
        allowed_paths={"contract"},
    )
    assert result == {}


def test_payload_defaults_to_allowlisted_nested_fields_only():
    result = sanitize_model_payload(
        {"kind": "positions", "metrics": {"quantity": "2", "secret": "x"}, "grant": "token"},
        allowed_paths={"kind", "metrics.quantity"},
    )
    assert result == {"kind": "positions", "metrics": {"quantity": "2"}}


def test_model_destination_requires_exact_https_host():
    assert validate_model_destination(
        "https://api.deepseek.com/v1", allowed_hosts={"api.deepseek.com"}
    ) == "https://api.deepseek.com/v1"
    with pytest.raises(EgressDenied):
        validate_model_destination("http://127.0.0.1:8000", allowed_hosts={"127.0.0.1"})
    with pytest.raises(EgressDenied):
        validate_model_destination("https://evil.example", allowed_hosts={"api.deepseek.com"})


def test_exception_redaction_does_not_export_message_or_headers():
    result = redact_exception(RuntimeError("Authorization: Bearer secret; customer=Alice"))
    assert result == {"code": "RuntimeError"}


def test_nested_trace_metadata_redacts_request_material():
    result = redact_trace_metadata({
        "safe": "ok",
        "nested": {"headers": {"Authorization": "secret"}, "prompt": "raw", "count": 1},
    })
    assert result == {"safe": "ok", "nested": {"count": 1}}
