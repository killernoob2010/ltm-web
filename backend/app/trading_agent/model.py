"""DeepSeek Chat Completions adapter with bounded, non-persistent reasoning."""
from dataclasses import dataclass
import json
import math
import os
from typing import Any

import requests

from pydantic import Field
from .contracts import StrictModel


class ModelError(RuntimeError):
    retryable = False


class AuthenticationError(ModelError):
    pass


class RetryableModelError(ModelError):
    retryable = True


class InvalidModelResponse(ModelError):
    pass


class ToolCall(StrictModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ModelTurn(StrictModel):
    tool_calls: list[ToolCall] = Field(default_factory=list)
    content: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str = ""


def _reject_nonfinite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON value")
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)
    if isinstance(value, list):
        for item in value:
            _reject_nonfinite(item)
    return value


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return _reject_nonfinite(raw)
    if not isinstance(raw, str) or len(raw) > 16000:
        raise InvalidModelResponse("工具参数格式无效")

    def pairs(pairs_list):
        output = {}
        for key, value in pairs_list:
            if key in output:
                raise InvalidModelResponse("工具参数包含重复字段")
            output[key] = value
        return output

    try:
        parsed = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidModelResponse("工具参数不是有效JSON") from exc
    if not isinstance(parsed, dict):
        raise InvalidModelResponse("工具参数必须是对象")
    return _reject_nonfinite(parsed)


class DeepSeekModel:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str | None = None, session=None):
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.session = session or requests.Session()

    def next_turn(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout_seconds: float = 15) -> ModelTurn:
        if not self.api_key:
            raise AuthenticationError("DeepSeek 尚未配置")
        if not isinstance(messages, list) or not isinstance(tools, list):
            raise ValueError("模型输入格式无效")
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        }
        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=min(15, max(0.1, float(timeout_seconds))),
            )
        except requests.RequestException as exc:
            raise RetryableModelError("DeepSeek 连接超时或不可用") from exc
        if response.status_code in {401, 403}:
            raise AuthenticationError("DeepSeek 认证失败")
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableModelError(f"DeepSeek 暂时不可用（{response.status_code}）")
        if response.status_code >= 400:
            raise ModelError(f"DeepSeek 请求失败（{response.status_code}）")
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise InvalidModelResponse("DeepSeek 返回格式无效") from exc
        tool_calls = []
        for call in message.get("tool_calls") or []:
            try:
                function = call["function"]
                tool_calls.append(ToolCall(id=str(call["id"]), name=str(function["name"]), arguments=parse_tool_arguments(function["arguments"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidModelResponse("DeepSeek 工具调用格式无效") from exc
        return ModelTurn(tool_calls=tool_calls, content=message.get("content"), usage=body.get("usage") or {}, finish_reason=str(choice.get("finish_reason") or ""))
