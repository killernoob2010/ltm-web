"""No-network checks for the explicitly authorized synthetic live probe."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("balance", ["1.00", "0.00"])
async def test_probe_protocol_and_balance_gate(monkeypatch, capsys, balance):
    path = Path(__file__).resolve().parents[2] / "scripts/probe_pydantic_protocol.py"
    spec = importlib.util.spec_from_file_location("protocol_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    calls = []

    def respond(request):
        if request.url.path == "/user/balance":
            return probe.httpx2.Response(200, json={"balance_infos": [
                {"currency": "CNY", "total_balance": balance}]})
        body = json.loads(request.content)
        calls.append(body)
        name = "synthetic_add" if len(calls) == 1 else next(
            t["function"]["name"] for t in body["tools"]
            if t["function"]["name"] != "synthetic_add")
        args = {"a": 17, "b": 25} if len(calls) == 1 else {
            "total": 42, "summary": "合成加法结果为四十二。"}
        return probe.httpx2.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 1,
            "model": "deepseek-flash", "choices": [{"index": 0,
            "finish_reason": "tool_calls", "message": {"role": "assistant",
            "content": None, "tool_calls": [{"id": str(len(calls)),
            "type": "function", "function": {"name": name,
            "arguments": json.dumps(args)}}]}}], "usage": {
            "prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})

    original = probe.httpx2.AsyncClient

    class MockClient(original):
        def __init__(self, **kwargs):
            super().__init__(transport=probe.httpx2.MockTransport(respond), **kwargs)

    monkeypatch.setattr(probe.httpx2, "AsyncClient", MockClient)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-only")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    if balance == "0.00":
        with pytest.raises(ValueError, match="Insufficient balance"):
            await probe.probe()
        assert calls == []
    else:
        assert await probe.probe()
        assert len(calls) == 2
        output = capsys.readouterr().out
        assert "synthetic-test-only" not in output
        assert json.loads(output)["peak_price_cost_upper_cny"] == "0.00088"
