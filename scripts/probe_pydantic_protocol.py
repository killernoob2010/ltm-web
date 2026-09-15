"""Explicit, synthetic-only live probe. Requires the approved isolated SDK environment."""
import argparse
import asyncio
import json
import os
from decimal import Decimal

import httpx2
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.trading_agent.pydantic_runtime import create_sdk_model


async def probe():
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    name = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
    if not key or name not in {"deepseek-flash", "deepseek-v4-flash"}:
        raise ValueError("Missing protected key or unreviewed model")
    if base not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
        raise ValueError("Unreviewed model endpoint")
    attempts = []
    tool_calls = []
    responses = []

    async def guard(request):
        if request.url.host != "api.deepseek.com" or request.url.scheme != "https":
            raise ValueError("Unapproved destination")
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            if len(attempts) >= 2 or len(request.content) > 16384:
                raise ValueError("Probe budget exceeded")
            if body.get("model") != name or body.get("thinking") != {"type": "disabled"}:
                raise ValueError("Unexpected model settings")
            if body.get("max_completion_tokens", body.get("max_tokens")) != 4096:
                raise ValueError("Unexpected output cap")
            attempts.append({"body_bytes": len(request.content)})
        elif request.url.path != "/user/balance" or request.method != "GET":
            raise ValueError("Unapproved request")

    async def record(response):
        if response.request.url.path.endswith("/chat/completions"):
            await response.aread()
            item = {"status": response.status_code}
            if response.status_code == 200:
                data = response.json()
                item.update(model=data.get("model"), usage=data.get("usage"))
            responses.append(item)

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        total: int
        summary: str

    async with httpx2.AsyncClient(verify=True, trust_env=False, follow_redirects=False,
                                 timeout=15, event_hooks={"request": [guard], "response": [record]}) as client:
        balance = await client.get("https://api.deepseek.com/user/balance",
                                   headers={"Authorization": f"Bearer {key}"})
        balance.raise_for_status()
        available = sum(Decimal(item["total_balance"]) for item in balance.json()["balance_infos"]
                        if item["currency"] == "CNY")
        # Two requests, <=16384 input bytes each, <=4096 output tokens each;
        # peak Flash prices 2/8 CNY per million tokens, with a conservative reserve.
        if available < Decimal("0.20"):
            raise ValueError("Insufficient balance for reserved maximum cost")
        model = create_sdk_model(api_key=key, base_url=base, model_name=name, http_client=client)
        agent = Agent(model, output_type=Answer, retries=0, instructions=(
            "This is a synthetic protocol test. Call synthetic_add exactly once with a=17,b=25. "
            "Then return the tool total and a short Chinese summary via the structured output."))

        @agent.tool_plain
        def synthetic_add(a: int, b: int) -> dict[str, int]:
            if tool_calls or (a, b) != (17, 25):
                raise ValueError("Unexpected synthetic tool arguments")
            tool_calls.append({"a": a, "b": b})
            return {"total": a + b}

        result = None
        error = None
        try:
            async with asyncio.timeout(40):
                result = await agent.run("执行合成加法验证。", usage_limits=UsageLimits(request_limit=2, tool_calls_limit=2))
        except Exception as exc:
            error = type(exc).__name__
        passed = bool(result and result.output.total == 42 and len(tool_calls) == 1
                      and any("\u4e00" <= ch <= "\u9fff" for ch in result.output.summary))
        usage = result.usage if result else None
        estimate = ((Decimal(usage.input_tokens) * 2 + Decimal(usage.output_tokens) * 8)
                    / 1_000_000 if usage else None)
        print(json.dumps({"protocol_pass": passed, "configured_model": name,
                          "model_requests": len(attempts), "tool_calls": len(tool_calls),
                          "responses": responses, "error_type": error,
                          "answer": result.output.model_dump() if result else None,
                          "peak_price_cost_upper_cny": str(estimate) if estimate is not None else None,
                          "reserved_max_cny": "0.20", "business_acceptance": "not_evaluated"}, ensure_ascii=False))
        return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    if not parser.parse_args().execute:
        parser.error("Live execution requires explicit --execute and approved budget")
    try:
        raise SystemExit(0 if asyncio.run(probe()) else 1)
    except Exception as exc:
        print(json.dumps({"protocol_pass": False, "error_type": type(exc).__name__}))
        raise SystemExit(1)
