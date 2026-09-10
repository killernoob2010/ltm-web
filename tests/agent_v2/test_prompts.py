import json
from datetime import datetime, timezone

from app.trading_agent import answer, prompts
from app.trading_agent.contracts import ToolEnvelope


def test_repair_messages_keep_draft_as_assistant_and_feedback_as_system():
    raw = '{"status":"complete","paragraphs":[],"note":"SECRET_CANARY_123"}'
    messages = prompts.build_answer_repair_messages(
        raw,
        (answer.AnswerIssue("extra_field", "/note", "该字段不允许出现在此位置。"),),
    )
    assert [item["role"] for item in messages] == ["assistant", "system"]
    assert messages[0]["content"] == raw
    assert "extra_field" in messages[1]["content"]
    assert "/note" in messages[1]["content"]
    assert "SECRET_CANARY_123" not in messages[1]["content"]


def test_repair_messages_bound_draft_and_issue_count():
    raw = "x" * 8001
    issues = [answer.AnswerIssue("invalid_value", f"/p/{index}", "受控问题") for index in range(7)]
    messages = prompts.build_answer_repair_messages(raw, issues)
    assert messages[0]["content"] == "[上一份答案超过修复上下文长度上限，正文已省略]"
    assert messages[1]["content"].count("invalid_value") == 5


def test_prompt_example_is_valid_answer_draft_and_time_semantics_are_explicit():
    parsed = answer.parse_answer(json.loads(prompts.ANSWER_EXAMPLE))
    assert parsed.status == "complete"
    assert "captured_at" in prompts.SYSTEM_PROMPT
    assert "data_as_of 未提供时必须明确未知" in prompts.SYSTEM_PROMPT
    assert "工具结果为 partial 时" in prompts.SYSTEM_PROMPT
    assert "账户、合约和多空方向" in prompts.SYSTEM_PROMPT
    assert "/payload/groups/0/metrics/quantity" in prompts.SYSTEM_PROMPT


def test_prompt_distinguishes_registered_public_refs_from_urls():
    messages = prompts.build_messages([], {"tools": []}, user_text="解释公开方法")
    content = messages[0]["content"]
    assert "research_uuid#/sources/index" in content
    assert "public_read_uuid#/payload/text" in content
    assert "不能直接放 URL" in content


def test_projected_tool_result_preserves_unknown_data_as_of():
    captured = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    envelope = ToolEnvelope(
        status="complete",
        captured_at=captured,
        data_as_of=None,
        calculation_version="test",
    )
    projected = json.loads(prompts.project_tool_result(envelope))
    assert projected["captured_at"].startswith("2026-09-09T12:00:00")
    assert projected["data_as_of"] is None


def test_projected_tool_result_is_valid_json_and_marks_group_truncation():
    groups = [{
        "dimensions": {"contract": f"i{index:04d}", "direction": "买"},
        "metrics": {"quantity": {
            "value": "1", "unit": "手", "status": "complete",
            "covered_rows": 1, "eligible_rows": 1,
        }},
        "note": "x" * 240,
    } for index in range(300)]
    envelope = ToolEnvelope(
        status="complete", captured_at=datetime.now(timezone.utc), calculation_version="test",
        payload={"kind": "positions", "count": 300, "groups": groups, "group_count": 300},
    )

    projected = json.loads(prompts.project_tool_result(envelope))
    assert len(prompts.project_tool_result(envelope)) <= 16000
    assert projected["payload"]["groups_truncated"] is True
    assert len(projected["payload"]["groups"]) < len(groups)
