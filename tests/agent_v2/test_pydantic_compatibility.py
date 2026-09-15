import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_sdk_factory_creates_openai_compatible_model_without_next_turn():
    from httpx2 import AsyncClient
    from pydantic_ai.models.openai import OpenAIChatModel

    from app.trading_agent.pydantic_runtime import create_sdk_model

    http_client = AsyncClient(verify=True, follow_redirects=False, trust_env=False)
    try:
        model = create_sdk_model(
            api_key="synthetic-key",
            base_url="https://api.deepseek.com/v1",
            model_name="deepseek-v4-flash",
            http_client=http_client,
        )
    finally:
        await http_client.aclose()

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "deepseek-v4-flash"
    assert not hasattr(model, "next_turn")
    assert "synthetic-key" not in repr(model)
    assert model._settings == {
        "thinking": False,
        "max_tokens": 4096,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    assert model._provider.client.max_retries == 0
    assert http_client.trust_env is False
    assert http_client.follow_redirects is False


@pytest.mark.asyncio
async def test_sdk_factory_uses_fake_http_payload_without_real_network(monkeypatch):
    from httpx2 import AsyncClient, MockTransport, Response
    from pydantic_ai import Agent, models

    from app.trading_agent.pydantic_runtime import create_sdk_model

    requests = []

    async def respond(request):
        requests.append(request)
        return Response(
            200,
            json={
                "id": "chatcmpl-synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "合成协议正常"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            request=request,
        )

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    http_client = AsyncClient(
        transport=MockTransport(respond),
        verify=True,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        model = create_sdk_model(
            api_key="synthetic-key",
            base_url="https://example.test/v1",
            model_name="deepseek-v4-flash",
            http_client=http_client,
        )
        result = await Agent(model, output_type=str).run("请返回协议测试结果")
    finally:
        await http_client.aclose()

    assert result.output == "合成协议正常"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://example.test/v1/chat/completions"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["max_completion_tokens"] == 4096
    assert payload["thinking"] == {"type": "disabled"}
    assert requests[0].headers["x-stainless-retry-count"] == "0"


@pytest.mark.asyncio
async def test_function_model_rejects_unknown_output_fields(monkeypatch):
    from pydantic import BaseModel, ConfigDict
    from pydantic_ai import Agent, ModelResponse, TextPart, models
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from pydantic_ai.models.function import FunctionModel

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        summary: str

    def respond(messages, info):
        return ModelResponse(parts=[TextPart('{"summary":"库存","unexpected":"x"}')])

    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    agent = Agent(FunctionModel(respond), output_type=Answer)

    with pytest.raises(UnexpectedModelBehavior):
        await agent.run("请校验未知字段")


@pytest.mark.asyncio
async def test_function_model_rejects_empty_response_offline(monkeypatch):
    from pydantic_ai import Agent, ModelResponse, models
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        return ModelResponse(parts=[])

    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    agent = Agent(FunctionModel(respond), output_type=str)

    with pytest.raises(UnexpectedModelBehavior):
        await agent.run("请处理空响应")


@pytest.mark.asyncio
async def test_function_model_serializes_tool_arguments_and_returns_offline(monkeypatch):
    from pydantic_ai import Agent, ModelResponse, TextPart, ToolCallPart, models
    from pydantic_ai.models.function import FunctionModel

    model_calls = []

    def respond(messages, info):
        model_calls.append(messages)
        if len(model_calls) == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="lookup",
                args={"symbol": "铁矿石", "limit": 2},
                tool_call_id="call-1",
            )])
        return ModelResponse(parts=[TextPart("已读取 2 条")])

    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    agent = Agent(FunctionModel(respond), output_type=str)

    @agent.tool_plain
    def lookup(symbol: str, limit: int) -> dict[str, object]:
        assert symbol == "铁矿石"
        assert limit == 2
        return {"symbol": symbol, "rows": [{"quantity": "2"}]}

    result = await agent.run("查询铁矿石")

    assert result.output == "已读取 2 条"
    assert len(model_calls) == 2
    assert model_calls[1][-1].parts[0].content == {
        "symbol": "铁矿石",
        "rows": [{"quantity": "2"}],
    }


@pytest.mark.asyncio
async def test_function_model_returns_structured_chinese_output_offline(monkeypatch):
    from pydantic import BaseModel, ConfigDict
    from pydantic_ai import Agent, ModelResponse, TextPart, models
    from pydantic_ai.models.function import FunctionModel

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        summary: str

    def respond(messages, info):
        return ModelResponse(parts=[TextPart('{"summary":"库存数据可核验"}')])

    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    agent = Agent(FunctionModel(respond), output_type=Answer)

    result = await agent.run("请用中文返回结构化摘要")

    assert result.output.summary == "库存数据可核验"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, "AuthenticationError"),
        (429, "RateLimitError"),
        (500, "InternalServerError"),
    ],
)
async def test_openai_compatible_http_errors_are_not_retried(status_code, error_type):
    from httpx2 import AsyncClient, MockTransport, Response
    from openai import AsyncOpenAI

    requests = []

    async def respond(request):
        requests.append(request)
        return Response(status_code, json={"error": {"message": "synthetic"}}, request=request)

    http_client = AsyncClient(
        transport=MockTransport(respond),
        verify=True,
        follow_redirects=False,
        trust_env=False,
    )
    client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url="https://example.test/v1",
        http_client=http_client,
        max_retries=0,
    )
    try:
        with pytest.raises(Exception) as error:
            await client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": "test"}],
            )
    finally:
        await http_client.aclose()

    assert type(error.value).__name__ == error_type
    assert error.value.request is not None
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_openai_compatible_timeout_is_reported_without_retry():
    from httpx2 import AsyncClient, MockTransport, ReadTimeout
    from openai import APITimeoutError, AsyncOpenAI

    requests = []

    async def respond(request):
        requests.append(request)
        raise ReadTimeout("synthetic timeout", request=request)

    http_client = AsyncClient(
        transport=MockTransport(respond),
        verify=True,
        follow_redirects=False,
        trust_env=False,
    )
    client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url="https://example.test/v1",
        http_client=http_client,
        max_retries=0,
    )
    try:
        with pytest.raises(APITimeoutError):
            await client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": "test"}],
            )
    finally:
        await http_client.aclose()

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_openai_compatible_request_propagates_cancellation():
    from httpx2 import AsyncClient, MockTransport
    from openai import AsyncOpenAI

    waiting = asyncio.Event()

    async def respond(request):
        await waiting.wait()
        raise AssertionError("cancelled request must not reach a response")

    http_client = AsyncClient(
        transport=MockTransport(respond),
        verify=True,
        follow_redirects=False,
        trust_env=False,
    )
    client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url="https://example.test/v1",
        http_client=http_client,
        max_retries=0,
    )
    task = asyncio.create_task(client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "test"}],
    ))
    try:
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await http_client.aclose()
