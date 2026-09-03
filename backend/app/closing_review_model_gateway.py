"""Bounded model access for closing review intent classification.

The model is allowed to classify a user's question into one of the fixed
closing-review profiles.  It never receives raw settlement rows, credentials,
database details, or enough authority to calculate a business result.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Literal, Optional, Protocol

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_CALLS = 2
MAX_CONTEXT_MESSAGES = 12

TaskProfile = Literal[
    "option_position_query",
    "option_previous_trading_day_position",
    "option_realized_pnl_query",
    "option_unrealized_pnl_query",
    "option_pnl_fact_attribution",
    "review_data_status_query",
    "effective_rule_query",
    "report_evidence_explanation",
    "unsupported",
]

TASK_PROFILE_DESCRIPTIONS = {
    "option_position_query": "查询指定交易日的铁矿石期权持仓",
    "option_previous_trading_day_position": "查询上一实际交易日的铁矿石期权持仓",
    "option_realized_pnl_query": "查询指定交易日真实平仓盈亏",
    "option_unrealized_pnl_query": "查询指定交易日持仓浮盈浮亏",
    "option_pnl_fact_attribution": "按合约组解释平仓盈亏的事实贡献",
    "review_data_status_query": "解释复盘数据的缺失、冲突或完整性状态",
    "effective_rule_query": "查询当前结果采用的计算规则和估值口径",
    "report_evidence_explanation": "解释当前对话中被指代结果的组成、公式和证据",
    "unsupported": "超出上述固定收盘复盘范围的问题",
}

_SAFE_CONTEXT_ROLES = {"user", "assistant"}
_SAFE_MESSAGE_TYPES = {
    "user",
    "answer",
    "automatic_result",
    "clarification",
    "unsupported",
}
_SAFE_ANCHOR_KEYS = {"task_profile", "target_date", "displayed_metric_labels", "result_ref"}
_ALLOWED_PROFILES = set(TASK_PROFILE_DESCRIPTIONS)


class IntentResolution(BaseModel):
    """The only model output accepted by the Agent workflow."""

    model_config = ConfigDict(extra="forbid")

    task_profile: TaskProfile
    date_expression: Optional[str] = None
    reference_mode: Literal["explicit_date", "latest_result", "none"]
    needs_clarification: bool
    clarification_question: Optional[str] = None

    @model_validator(mode="after")
    def validate_clarification_contract(self) -> "IntentResolution":
        has_question = bool(self.clarification_question and self.clarification_question.strip())
        if self.needs_clarification and not has_question:
            raise ValueError("clarification_question is required when needs_clarification is true")
        if not self.needs_clarification and self.clarification_question is not None:
            raise ValueError("clarification_question must be null unless clarification is needed")
        if has_question:
            self.clarification_question = self.clarification_question.strip()
        return self


class IntentRequest(BaseModel):
    """Safe input envelope shared by fake and real providers."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default="closing-review-intent", min_length=1, max_length=128)
    user_text: str = Field(min_length=1, max_length=1000)
    context_messages: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    anchor: Optional[dict[str, Any]] = None
    conversation_id: Optional[int] = None


class ModelGatewayError(RuntimeError):
    """Stable, non-sensitive provider error exposed to the workflow."""

    def __init__(
        self,
        category: str,
        retryable: bool,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.category = category
        self.retryable = retryable
        self.metadata = dict(metadata or {})
        super().__init__(f"closing review model gateway error: {category}")


class ClosingReviewModelProvider(Protocol):
    def resolve_intent(self, request: IntentRequest) -> IntentResolution:
        ...


def _safe_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]


def _safe_anchor(anchor: Any) -> Optional[dict[str, Any]]:
    if not isinstance(anchor, dict):
        return None
    safe: dict[str, Any] = {}
    task_profile = anchor.get("task_profile")
    if isinstance(task_profile, str) and task_profile in _ALLOWED_PROFILES:
        safe["task_profile"] = task_profile
    target_date = anchor.get("target_date")
    if isinstance(target_date, str) and target_date.strip():
        safe["target_date"] = target_date.strip()[:16]
    labels = anchor.get("displayed_metric_labels")
    if labels is None:
        labels = anchor.get("metric_labels")
    if isinstance(labels, str):
        labels = [labels]
    if isinstance(labels, list):
        safe["displayed_metric_labels"] = [
            _safe_text(item, 80) for item in labels[:8] if _safe_text(item, 80)
        ]
    result_ref = anchor.get("result_ref")
    if isinstance(result_ref, str) and result_ref.strip():
        safe["result_ref"] = result_ref.strip()[:128]
    return safe or None


def _safe_context_messages(request: IntentRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for raw in request.context_messages:
        if not isinstance(raw, dict):
            continue
        expected_conversation = request.conversation_id
        raw_conversation = raw.get("conversation_id")
        if (
            expected_conversation is not None
            and raw_conversation is not None
            and str(raw_conversation) != str(expected_conversation)
        ):
            continue
        role = raw.get("role")
        if role not in _SAFE_CONTEXT_ROLES:
            continue
        content = _safe_text(raw.get("content"), 2000)
        if not content:
            continue
        message_type = raw.get("message_type")
        if message_type not in _SAFE_MESSAGE_TYPES:
            message_type = "user" if role == "user" else "answer"
        created_at = _safe_text(raw.get("created_at"), 32)
        item = {"role": role, "message_type": message_type, "content": content}
        if created_at:
            item["created_at"] = created_at.split(".", 1)[0]
        messages.append(item)
    return messages[-MAX_CONTEXT_MESSAGES:]


def _sanitize_request(request: IntentRequest) -> IntentRequest:
    return IntentRequest(
        request_id=request.request_id,
        user_text=request.user_text.strip()[:1000],
        context_messages=_safe_context_messages(request),
        anchor=_safe_anchor(request.anchor),
        conversation_id=request.conversation_id,
    )


def _safe_usage(value: Any) -> Optional[dict[str, int]]:
    if not isinstance(value, dict):
        return None
    allowed = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    }
    result: dict[str, int] = {}
    for key in allowed:
        raw = value.get(key)
        if isinstance(raw, bool):
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            result[key] = number
    return result or None


class FakeClosingReviewProvider:
    """Deterministic test adapter with the same input minimization as DeepSeek."""

    def __init__(
        self,
        resolution: Optional[IntentResolution] = None,
        resolver: Optional[Callable[[IntentRequest], IntentResolution]] = None,
    ) -> None:
        self.resolution = resolution or IntentResolution(
            task_profile="unsupported",
            reference_mode="none",
            needs_clarification=False,
        )
        self.resolver = resolver
        self.last_request: Optional[IntentRequest] = None
        self.last_metadata: dict[str, Any] = {
            "provider": "fake",
            "model": "fake-closing-review",
            "attempt_count": 0,
        }

    def resolve_intent(self, request: IntentRequest) -> IntentResolution:
        safe_request = _sanitize_request(request)
        self.last_request = safe_request
        self.last_metadata = {
            "provider": "fake",
            "model": "fake-closing-review",
            "attempt_count": 1,
        }
        result = self.resolver(safe_request) if self.resolver else self.resolution
        return IntentResolution.model_validate(result)


SYSTEM_PROMPT = """You classify one user question for a deterministic closing-review service.
Return one JSON object only. The word JSON is intentional: do not write prose,
Markdown, calculations, numbers, account identifiers, SQL, tools, or business
conclusions. Choose exactly one task_profile from the following fixed list:
{profiles}

The JSON keys are exactly:
task_profile, date_expression, reference_mode, needs_clarification,
clarification_question.
reference_mode is one of explicit_date, latest_result, none. If a date or
reference cannot be uniquely understood, set needs_clarification to true and
ask exactly one short clarification question; otherwise set it to false and
clarification_question to null. Do not invent a date.
""".format(
    profiles="\n".join(
        f"- {name}: {description}" for name, description in TASK_PROFILE_DESCRIPTIONS.items()
    )
)


REPAIR_PROMPT = (
    "上一次输出未通过服务端 JSON Schema 校验。请仅返回符合指定字段的 JSON 对象，"
    "不要解释、不要 Markdown、不要增加字段。"
)


class DeepSeekClosingReviewProvider:
    """OpenAI-compatible DeepSeek adapter with a hard two-call budget."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        session: Any = None,
        timeout_seconds: Optional[float] = None,
        clock: Any = None,
        request_sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.api_base = (api_base or os.getenv("DEEPSEEK_API_BASE") or DEFAULT_API_BASE).strip().rstrip("/")
        self.model = (model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()
        raw_timeout = timeout_seconds
        if raw_timeout is None:
            raw_timeout = os.getenv("DEEPSEEK_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            configured_timeout = float(raw_timeout)
        except (TypeError, ValueError):
            configured_timeout = DEFAULT_TIMEOUT_SECONDS
        self.timeout_seconds = min(DEFAULT_TIMEOUT_SECONDS, max(1.0, configured_timeout))
        self.session = session or requests.Session()
        self.clock = clock or time
        self.request_sleep = request_sleep
        self.last_metadata: dict[str, Any] = {
            "provider": "deepseek",
            "model": self.model,
            "attempt_count": 0,
        }

    @property
    def endpoint(self) -> str:
        return f"{self.api_base}{CHAT_COMPLETIONS_PATH}"

    def _monotonic(self) -> float:
        value = self.clock() if callable(self.clock) else self.clock.monotonic()
        return float(value)

    def _record_metadata(
        self,
        request_id: str,
        attempt_count: int,
        started_at: float,
        *,
        status_category: Optional[str] = None,
        error_category: Optional[str] = None,
        usage: Any = None,
        finish_reason: Any = None,
    ) -> None:
        duration_seconds = int(max(0, round(self._monotonic() - started_at)))
        metadata: dict[str, Any] = {
            "provider": "deepseek",
            "model": self.model,
            "attempt_count": attempt_count,
            "duration_seconds": duration_seconds,
        }
        if status_category:
            metadata["status_category"] = status_category
        if error_category:
            metadata["error_category"] = error_category
        safe_usage = _safe_usage(usage)
        if safe_usage is not None:
            metadata["usage"] = safe_usage
        if isinstance(finish_reason, str) and finish_reason:
            metadata["finish_reason"] = finish_reason[:64]
        self.last_metadata = metadata
        logger.info(
            "closing_review_model_call request_id=%s provider=%s model=%s status=%s attempts=%d duration_seconds=%d",
            _safe_text(request_id, 128),
            metadata["provider"],
            metadata["model"],
            metadata.get("error_category") or metadata.get("status_category") or "unknown",
            attempt_count,
            duration_seconds,
        )

    @staticmethod
    def _response_content(response: Any) -> tuple[str, Any, Any]:
        try:
            body = response.json()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("response body is not JSON") from exc
        if not isinstance(body, dict):
            raise ValueError("response body is not an object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("response choices are missing")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("response content is missing")
        content = message["content"].strip()
        if not content:
            raise ValueError("response content is empty")
        usage = body.get("usage")
        finish_reason = choice.get("finish_reason")
        return content, usage, finish_reason

    @staticmethod
    def _parse_resolution(content: str) -> IntentResolution:
        try:
            value = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("response content is not JSON") from exc
        return IntentResolution.model_validate(value)

    def _payload(self, request: IntentRequest, repair: bool = False) -> dict[str, Any]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": request.user_text,
                        "context_messages": request.context_messages,
                        "anchor": request.anchor,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        if repair:
            messages.append({"role": "user", "content": REPAIR_PROMPT})
        return {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": 256,
            "stream": False,
        }

    def _raise_retry_exhausted(
        self,
        request_id: str,
        attempt_count: int,
        started_at: float,
        category: str,
    ) -> None:
        self._record_metadata(
            request_id,
            attempt_count,
            started_at,
            status_category=category,
            error_category="temporarily_unavailable",
        )
        raise ModelGatewayError(
            "temporarily_unavailable",
            retryable=False,
            metadata=self.last_metadata,
        )

    def resolve_intent(self, request: IntentRequest) -> IntentResolution:
        safe_request = _sanitize_request(request)
        if not self.api_key:
            self._record_metadata(
                safe_request.request_id,
                0,
                self._monotonic(),
                error_category="missing_configuration",
            )
            raise ModelGatewayError(
                "missing_configuration",
                retryable=False,
                metadata=self.last_metadata,
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        repair = False
        for attempt in range(1, MAX_CALLS + 1):
            started_at = self._monotonic()
            try:
                response = self.session.post(
                    self.endpoint,
                    headers=headers,
                    json=self._payload(safe_request, repair=repair),
                    timeout=self.timeout_seconds,
                )
            except requests.exceptions.ConnectTimeout:
                category = "connection_timeout"
                if attempt < MAX_CALLS:
                    self._record_metadata(safe_request.request_id, attempt, started_at, status_category=category)
                    continue
                self._raise_retry_exhausted(safe_request.request_id, attempt, started_at, category)
            except requests.exceptions.ReadTimeout:
                category = "read_timeout"
                if attempt < MAX_CALLS:
                    self._record_metadata(safe_request.request_id, attempt, started_at, status_category=category)
                    continue
                self._raise_retry_exhausted(safe_request.request_id, attempt, started_at, category)
            except requests.exceptions.Timeout:
                category = "read_timeout"
                if attempt < MAX_CALLS:
                    self._record_metadata(safe_request.request_id, attempt, started_at, status_category=category)
                    continue
                self._raise_retry_exhausted(safe_request.request_id, attempt, started_at, category)
            except requests.exceptions.ConnectionError:
                category = "connection_timeout"
                if attempt < MAX_CALLS:
                    self._record_metadata(safe_request.request_id, attempt, started_at, status_category=category)
                    continue
                self._raise_retry_exhausted(safe_request.request_id, attempt, started_at, category)
            except requests.exceptions.RequestException:
                category = "connection_timeout"
                if attempt < MAX_CALLS:
                    self._record_metadata(safe_request.request_id, attempt, started_at, status_category=category)
                    continue
                self._raise_retry_exhausted(safe_request.request_id, attempt, started_at, category)

            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 429:
                if attempt < MAX_CALLS:
                    self._record_metadata(
                        safe_request.request_id,
                        attempt,
                        started_at,
                        status_category="rate_limited",
                    )
                    continue
                self._raise_retry_exhausted(
                    safe_request.request_id,
                    attempt,
                    started_at,
                    "rate_limited",
                )
            if 500 <= status_code <= 599:
                if attempt < MAX_CALLS:
                    self._record_metadata(
                        safe_request.request_id,
                        attempt,
                        started_at,
                        status_category="provider_5xx",
                    )
                    continue
                self._raise_retry_exhausted(
                    safe_request.request_id,
                    attempt,
                    started_at,
                    "provider_5xx",
                )
            if 400 <= status_code <= 499:
                self._record_metadata(
                    safe_request.request_id,
                    attempt,
                    started_at,
                    status_category="provider_4xx",
                    error_category="provider_4xx",
                )
                raise ModelGatewayError(
                    "provider_4xx",
                    retryable=False,
                    metadata=self.last_metadata,
                )
            if status_code < 200 or status_code >= 300:
                if attempt < MAX_CALLS:
                    self._record_metadata(
                        safe_request.request_id,
                        attempt,
                        started_at,
                        status_category="provider_5xx",
                    )
                    continue
                self._raise_retry_exhausted(
                    safe_request.request_id,
                    attempt,
                    started_at,
                    "provider_5xx",
                )

            try:
                content, usage, finish_reason = self._response_content(response)
                resolution = self._parse_resolution(content)
            except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
                if attempt < MAX_CALLS:
                    repair = True
                    self._record_metadata(
                        safe_request.request_id,
                        attempt,
                        started_at,
                        status_category="invalid_schema",
                    )
                    continue
                self._record_metadata(
                    safe_request.request_id,
                    attempt,
                    started_at,
                    status_category="invalid_schema",
                    error_category="invalid_schema",
                )
                raise ModelGatewayError(
                    "invalid_schema",
                    retryable=False,
                    metadata=self.last_metadata,
                )
            self._record_metadata(
                safe_request.request_id,
                attempt,
                started_at,
                status_category="success",
                usage=usage,
                finish_reason=finish_reason,
            )
            return resolution

        raise ModelGatewayError("temporarily_unavailable", retryable=False, metadata=self.last_metadata)


def build_closing_review_provider() -> ClosingReviewModelProvider:
    provider_name = os.getenv("CLOSING_REVIEW_AGENT_PROVIDER", "fake").strip().lower()
    if provider_name == "deepseek":
        return DeepSeekClosingReviewProvider()
    return FakeClosingReviewProvider()
