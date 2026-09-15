"""Fail-closed filtering for values that may be sent to a model or tracer."""
from __future__ import annotations

import ipaddress
import math
from typing import Any, Iterable
from urllib.parse import urlparse


class EgressDenied(ValueError):
    """A model payload or destination is outside the reviewed boundary."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


_FORBIDDEN_KEYS = {
    "authorization", "cookie", "set-cookie", "api_key", "apikey", "password",
    "secret", "token", "grant", "user_id", "account_id", "account_ids",
    "customer", "client", "customer_id", "customer_name", "user", "username",
    "account", "account_no", "order", "order_id", "order_no", "order_number",
    "contract", "contract_id", "contract_no", "execution_id",
    "permission", "permissions", "principal", "sql", "query_sql", "raw_sql",
    "command", "exception", "traceback", "headers", "request_headers", "body",
}
_MISSING = object()


def _normalized_rules(allowed_paths: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    rules = []
    for raw in allowed_paths:
        if not isinstance(raw, str):
            continue
        path = raw.strip().strip("/").replace("/", ".")
        parts = tuple(part for part in path.split(".") if part)
        if parts:
            rules.append(parts)
    return tuple(rules)


def _matches(path: tuple[str, ...], rules: tuple[tuple[str, ...], ...]) -> bool:
    return any(
        len(path) == len(rule)
        and all(expected == "*" or expected == actual for expected, actual in zip(rule, path))
        for rule in rules
    )


def _has_descendant(path: tuple[str, ...], rules: tuple[tuple[str, ...], ...]) -> bool:
    return any(
        len(rule) > len(path)
        and all(expected == "*" or expected == actual for expected, actual in zip(rule, path))
        for rule in rules
    )


def _sensitive(text: str, sensitive_values: tuple[str, ...]) -> bool:
    return any(value and value in text for value in sensitive_values)


def _assert_no_sensitive_values(value: Any, sensitive_values: tuple[str, ...], depth: int = 0) -> None:
    if depth > 8:
        raise EgressDenied("payload_depth_exceeded")
    if isinstance(value, str):
        if _sensitive(value, sensitive_values):
            raise EgressDenied("private_content")
        return
    if isinstance(value, dict):
        for child in value.values():
            _assert_no_sensitive_values(child, sensitive_values, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_sensitive_values(child, sensitive_values, depth + 1)


def _sanitize(value: Any, path: tuple[str, ...], rules: tuple[tuple[str, ...], ...],
              sensitive_values: tuple[str, ...], depth: int) -> Any:
    if depth > 8:
        raise EgressDenied("payload_depth_exceeded")
    if path and path[-1].casefold() in _FORBIDDEN_KEYS:
        raise EgressDenied("forbidden_field")
    if isinstance(value, str):
        if _sensitive(value, sensitive_values):
            raise EgressDenied("private_content")
        return value if _matches(path, rules) else _MISSING
    if value is None or isinstance(value, (bool, int)):
        return value if _matches(path, rules) else _MISSING
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EgressDenied("non_finite_value")
        return value if _matches(path, rules) else _MISSING
    if isinstance(value, dict):
        if _matches(path, rules) and not _has_descendant(path, rules):
            # An explicitly allowed object is still checked for forbidden field
            # names and sensitive values before it is copied.
            result = {}
            for key, child in value.items():
                key_text = str(key)
                if key_text.casefold() in _FORBIDDEN_KEYS:
                    raise EgressDenied("forbidden_field")
                checked = _sanitize(child, (*path, key_text), rules, sensitive_values, depth + 1)
                if checked is not _MISSING:
                    result[key_text] = checked
            return result
        result = {}
        for key, child in value.items():
            key_text = str(key)
            if key_text.casefold() in _FORBIDDEN_KEYS:
                # Default deny: a secret field is removed even when its parent
                # is otherwise model-visible.
                continue
            child_path = (*path, key_text)
            if not (_matches(child_path, rules) or _has_descendant(child_path, rules)):
                continue
            checked = _sanitize(child, child_path, rules, sensitive_values, depth + 1)
            if checked is not _MISSING:
                result[key_text] = checked
        return result if result or _matches(path, rules) else _MISSING
    if isinstance(value, (list, tuple)):
        if not (_matches(path, rules) or _has_descendant(path, rules)):
            return _MISSING
        result = []
        for item in value:
            item_path = (*path, "*")
            checked = _sanitize(item, item_path, rules, sensitive_values, depth + 1)
            if checked is not _MISSING:
                result.append(checked)
        return result
    return _MISSING


def sanitize_model_payload(payload: dict[str, Any], *, allowed_paths: Iterable[str],
                           sensitive_values: Iterable[str] = ()) -> dict[str, Any]:
    """Return only explicitly allowed fields, rejecting sensitive allowed values."""
    if not isinstance(payload, dict):
        raise EgressDenied("payload_not_object")
    rules = _normalized_rules(allowed_paths)
    sensitive = tuple(str(value) for value in sensitive_values if value not in (None, ""))
    _assert_no_sensitive_values(payload, sensitive)
    if not rules:
        return {}
    result = _sanitize(payload, (), rules, sensitive, 0)
    if result is _MISSING or not isinstance(result, dict):
        return {}
    return result


def validate_model_destination(url: str, *, allowed_hosts: Iterable[str]) -> str:
    """Validate a model endpoint without following redirects or private hosts."""
    if not isinstance(url, str):
        raise EgressDenied("invalid_destination")
    parsed = urlparse(url)
    try:
        hostname = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError as exc:
        raise EgressDenied("invalid_destination") from exc
    allowed = {str(host).strip().casefold() for host in allowed_hosts if str(host).strip()}
    if (
        parsed.scheme != "https" or not hostname or hostname not in allowed
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or port not in (None, 443)
    ):
        raise EgressDenied("destination_denied")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        raise EgressDenied("ip_destination_denied")
    return url


def redact_exception(exc: BaseException) -> dict[str, str]:
    """Keep a stable type/code for diagnostics without exporting the message."""
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code.isascii() or not code.replace("_", "").replace("-", "").replace(".", "").isalnum():
        code = type(exc).__name__
    return {"code": code[:64]}


def redact_trace_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remove request material from locally recorded diagnostic metadata."""
    if not isinstance(metadata, dict):
        return {}
    blocked = _FORBIDDEN_KEYS | {"prompt", "messages", "response", "raw_payload"}

    def redact(value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "[redacted]"
        if isinstance(value, dict):
            return {
                str(key): redact(child, depth + 1)
                for key, child in value.items()
                if str(key).casefold() not in blocked
            }
        if isinstance(value, list):
            return [redact(child, depth + 1) for child in value]
        if isinstance(value, tuple):
            return [redact(child, depth + 1) for child in value]
        return value

    return redact(metadata)


__all__ = [
    "EgressDenied", "sanitize_model_payload", "validate_model_destination",
    "redact_exception", "redact_trace_metadata",
]
