"""Behavior tests for the bounded closing review Agent workflow and API."""

import os
import sys
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import closing_review_agent as agent  # noqa: E402
from app import db, main  # noqa: E402
from app import closing_trading_review as review  # noqa: E402
from app.closing_review_model_gateway import IntentResolution  # noqa: E402


def _metadata(source="test-settlement", refs=True, data_as_of="2026-08-31T15:00:00+08:00"):
    return review.EvidenceMetadata(
        data_as_of=data_as_of,
        source=source,
        freshness="target_trading_date",
        completeness="complete",
        evidence_refs=[
            review.EvidenceRef(ref="statement:test", source="月结单", locator="第 1 行")
        ] if refs else [],
    )


def _fact(value, refs=True):
    return review.NumericFact(value=value, metadata=_metadata(refs=refs))


def full_report():
    detail = review.OptionPositionDetail(
        contract="i2609-c-700",
        strike_price=_fact(700),
        quantity_lots=_fact(2),
        floating_pnl=_fact(120),
    )
    group = review.OptionPositionGroup(
        expiry_month="2609",
        option_type="Call",
        direction="卖",
        strike_min=_fact(700),
        strike_max=_fact(700),
        quantity_lots=_fact(2),
        floating_pnl=_fact(120),
        contract_count=_fact(1),
        details=[detail],
    )
    attribution = review.OptionPnlAttribution(
        expiry_month="2609",
        option_type="Call",
        direction="卖",
        realized_close_pnl=_fact(300),
        contribution_ratio=_fact(1),
        ratio_interpretation="suitable",
    )
    return review.OptionDailyReviewResponse(
        status="complete",
        trading_date="20260831",
        account_name="宏源账户",
        instrument="铁矿石期权",
        valuation_basis="daily_settlement",
        valuation_note="按日结算价估值",
        position_availability="confirmed",
        call_net=review.NetPositionFact(
            direction_label="净卖",
            lots=_fact(2),
            tons=_fact(20),
            wan_tons=_fact(0.002),
        ),
        put_net=review.NetPositionFact(
            direction_label="净平",
            lots=_fact(0),
            tons=_fact(0),
            wan_tons=_fact(0),
        ),
        position_groups=[group],
        realized_close_pnl=_fact(300),
        unrealized_pnl=_fact(-120),
        pnl_attribution=[attribution],
        metadata=_metadata(),
        summary_text="完整测试复盘",
    )


def _intent(profile, date_expression="20260831", reference_mode="explicit_date"):
    return IntentResolution(
        task_profile=profile,
        date_expression=date_expression,
        reference_mode=reference_mode,
        needs_clarification=False,
    )


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "closing-review-agent-api.db")
    db.init_db()


def _insert_user(name, username, role="用户"):
    with db.connect() as conn:
        cur = conn.cursor()
        user_id = db._last_insert_id(
            cur,
            """
            INSERT INTO users (name, username, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, username, "期货组", db.password_hash("password"), role),
        )
    return int(user_id)


def _grant(user_id, module_code, can_view=1):
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT OR REPLACE INTO module_permissions
                (user_id, module_code, can_view, can_edit, can_sensitive)
            VALUES (?, ?, ?, 0, 0)
            """,
            (user_id, module_code, can_view),
        )


def _token(user_id):
    return db.create_session(user_id)


def _authorized_user(name):
    user_id = _insert_user(name, name.lower().replace(" ", "-"))
    _grant(user_id, "closing_review_agent")
    _grant(user_id, "trading_options")
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def test_resolve_task_request_keeps_profile_and_strict_explicit_date():
    request = agent.resolve_task_request(
        _intent("option_realized_pnl_query"),
        context={"messages": [], "anchor": None},
        now=datetime(2026, 9, 3, 12, 0),
    )

    assert request.task_profile == "option_realized_pnl_query"
    assert request.trading_date == "20260831"
    assert request.controlled_status is None


def test_resolve_task_request_does_not_guess_when_date_is_missing():
    request = agent.resolve_task_request(
        _intent("option_position_query", date_expression=None, reference_mode="none"),
        context={"messages": [], "anchor": None},
        now=datetime(2026, 9, 3, 12, 0),
    )

    assert request.controlled_status == "needs_clarification"
    assert request.clarification_question


@pytest.mark.parametrize(
    "profile, expected_present, expected_empty",
    [
        (
            "option_position_query",
            {"call_net", "put_net", "position_groups"},
            {"realized_close_pnl", "unrealized_pnl", "pnl_attribution"},
        ),
        (
            "option_previous_trading_day_position",
            {"call_net", "put_net", "position_groups"},
            {"realized_close_pnl", "unrealized_pnl", "pnl_attribution"},
        ),
        (
            "option_realized_pnl_query",
            {"realized_close_pnl"},
            {"call_net", "put_net", "position_groups", "unrealized_pnl", "pnl_attribution"},
        ),
        (
            "option_unrealized_pnl_query",
            {"unrealized_pnl"},
            {"call_net", "put_net", "position_groups", "realized_close_pnl", "pnl_attribution"},
        ),
        (
            "option_pnl_fact_attribution",
            {"pnl_attribution"},
            {"call_net", "put_net", "position_groups", "realized_close_pnl", "unrealized_pnl"},
        ),
        (
            "review_data_status_query",
            {"metadata", "warnings"},
            {"call_net", "put_net", "position_groups", "realized_close_pnl", "unrealized_pnl", "pnl_attribution"},
        ),
        (
            "effective_rule_query",
            {"valuation_basis", "valuation_note", "calculation_version", "rule_version"},
            {"call_net", "put_net", "position_groups", "realized_close_pnl", "unrealized_pnl", "pnl_attribution"},
        ),
    ],
)
def test_each_task_profile_projects_only_requested_business_fields(
    profile, expected_present, expected_empty
):
    projection = agent.project_task_result(profile, full_report(), None)
    payload = projection.model_dump(exclude_none=True)

    assert expected_present.issubset(payload)
    for field in expected_empty:
        assert field not in payload or payload[field] in ([], None)


def test_evidence_explanation_requires_a_unique_current_anchor():
    projection = agent.project_task_result("report_evidence_explanation", full_report(), None)

    assert projection.status == "needs_clarification"
    assert projection.clarification_question
    assert projection.realized_close_pnl is None
    assert projection.unrealized_pnl is None


def test_evidence_explanation_uses_anchor_profile_without_overanswering():
    projection = agent.project_task_result(
        "report_evidence_explanation",
        full_report(),
        {
            "task_profile": "option_realized_pnl_query",
            "target_date": "20260831",
            "displayed_metric_labels": ["真实平仓盈亏"],
            "result_ref": "task:previous",
        },
    )

    assert projection.realized_close_pnl is not None
    assert projection.unrealized_pnl is None
    assert projection.position_groups == []


def test_completion_validator_rejects_unproven_or_cross_profile_values():
    report = full_report()
    realized = agent.project_task_result("option_realized_pnl_query", report, None)
    unrealized = agent.project_task_result("option_unrealized_pnl_query", report, None)

    with pytest.raises(ValueError, match="evidence"):
        agent.validate_completion(
            realized.model_copy(update={"realized_close_pnl": _fact(1, refs=False)}),
            report,
        )
    with pytest.raises(ValueError, match="unrealized"):
        agent.validate_completion(
            realized.model_copy(update={"unrealized_pnl": _fact(1)}),
            report,
        )
    with pytest.raises(ValueError, match="realized"):
        agent.validate_completion(
            unrealized.model_copy(update={"realized_close_pnl": _fact(1)}),
            report,
        )


def test_completion_validator_rejects_account_actions_anomalies_and_fractional_times():
    report = full_report()
    projection = agent.project_task_result("option_realized_pnl_query", report, None)

    with pytest.raises(ValueError, match="account"):
        agent.validate_completion(projection.model_copy(update={"account_name": "其他账户"}), report)
    with pytest.raises(ValueError, match="交易"):
        agent.validate_completion(projection.model_copy(update={"controlled_message": "建议下单"}), report)
    anomaly_report = report.model_copy(update={"status": "data_anomaly"})
    with pytest.raises(ValueError, match="anomaly|异常"):
        agent.validate_completion(projection, anomaly_report)
    fractional_metadata = report.metadata.model_copy(update={"data_as_of": "2026-08-31T15:00:00.123+08:00"})
    with pytest.raises(ValueError, match="秒"):
        agent.validate_completion(projection.model_copy(update={"metadata": fractional_metadata}), report)


def test_render_answer_is_plain_text_and_keeps_realized_and_unrealized_separate():
    report = full_report()
    realized_text = agent.render_answer(
        agent.project_task_result("option_realized_pnl_query", report, None)
    )
    unrealized_text = agent.render_answer(
        agent.project_task_result("option_unrealized_pnl_query", report, None)
    )

    assert "真实平仓盈亏" in realized_text
    assert "持仓浮盈浮亏" not in realized_text
    assert "持仓浮盈浮亏" in unrealized_text
    assert "真实平仓盈亏" not in unrealized_text
    assert "<" not in realized_text


def test_message_permission_runs_before_model_and_data(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    user_id = _insert_user("No Agent", "no-agent")
    _grant(user_id, "trading_options")
    token = _token(user_id)
    monkeypatch.setattr(agent, "build_closing_review_provider", lambda: pytest.fail("model called"))
    monkeypatch.setattr(agent, "build_option_daily_review", lambda _date: pytest.fail("data called"))

    with TestClient(main.app) as client:
        response = client.post(
            "/api/closing-review-agent/conversations/1/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "昨天持仓怎么样", "client_request_id": str(uuid.uuid4())},
        )

    assert response.status_code == 403


def test_agent_api_rejects_cross_user_conversation_and_invalid_message(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    owner = _authorized_user("Owner")
    stranger = _authorized_user("Stranger")
    owner_token = _token(owner["id"])
    stranger_token = _token(stranger["id"])
    monkeypatch.setattr(agent, "build_closing_review_provider", lambda: agent.FakeClosingReviewProvider())

    with TestClient(main.app) as client:
        created = client.post(
            "/api/closing-review-agent/conversations",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"title": "所有者对话"},
        )
        conversation_id = created.json()["id"]
        cross_user = client.get(
            f"/api/closing-review-agent/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {stranger_token}"},
        )
        invalid_uuid = client.post(
            f"/api/closing-review-agent/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"content": "问题", "client_request_id": "not-a-uuid"},
        )
        long_content = client.post(
            f"/api/closing-review-agent/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"content": "x" * 1001, "client_request_id": str(uuid.uuid4())},
        )

    assert created.status_code == 200
    assert cross_user.status_code == 404
    assert invalid_uuid.status_code == 422
    assert long_content.status_code == 422


def test_agent_api_idempotency_stores_one_user_message_task_and_provider_call(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    user = _authorized_user("Idempotent")
    token = _token(user["id"])
    provider = agent.FakeClosingReviewProvider(
        IntentResolution(
            task_profile="option_realized_pnl_query",
            date_expression="20260831",
            reference_mode="explicit_date",
            needs_clarification=False,
        )
    )
    calls = {"provider": 0}

    def counted_provider():
        calls["provider"] += 1
        return provider

    monkeypatch.setattr(agent, "build_closing_review_provider", counted_provider)
    monkeypatch.setattr(agent, "build_option_daily_review", lambda _date: full_report())
    request_id = str(uuid.uuid4())
    payload = {"content": "2026年8月31日实际平仓盈亏是多少？", "client_request_id": request_id}

    with TestClient(main.app) as client:
        conversation = client.post(
            "/api/closing-review-agent/conversations",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "幂等测试"},
        ).json()
        first = client.post(
            f"/api/closing-review-agent/conversations/{conversation['id']}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        second = client.post(
            f"/api/closing-review-agent/conversations/{conversation['id']}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["task"]["id"] == second.json()["task"]["id"]
    assert calls["provider"] == 1
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM closing_review_messages WHERE role = 'user'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM closing_review_tasks").fetchone()[0] == 1
        task_row = conn.execute(
            "SELECT user_message_id, validated_intent FROM closing_review_tasks"
        ).fetchone()
        assert task_row["user_message_id"] is not None
        assert "option_realized_pnl_query" in task_row["validated_intent"]


def test_processing_duplicate_returns_same_task_without_second_provider_call(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    user = _authorized_user("Processing")
    token = _token(user["id"])
    conversation = agent.store.create_conversation(user["id"])
    request_id = str(uuid.uuid4())
    task, created = agent.store.claim_user_task(
        user["id"],
        conversation["id"],
        request_id,
        task_profile=None,
        state="processing",
    )
    assert created is True
    monkeypatch.setattr(agent, "build_closing_review_provider", lambda: pytest.fail("duplicate called model"))

    with TestClient(main.app) as client:
        response = client.post(
            f"/api/closing-review-agent/conversations/{conversation['id']}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "重复请求", "client_request_id": request_id},
        )

    assert response.status_code == 202
    assert response.json()["task"]["id"] == task["id"]
    assert response.json()["task"]["state"] == "processing"


def test_invalid_suggestion_is_rejected_before_creating_task_or_message(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    user = _authorized_user("BadSuggestion")
    token = _token(user["id"])
    conversation = agent.store.create_conversation(user["id"])

    with TestClient(main.app) as client:
        response = client.post(
            f"/api/closing-review-agent/conversations/{conversation['id']}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "suggestion_id": "not-a-real-suggestion",
                "client_request_id": str(uuid.uuid4()),
            },
        )

    assert response.status_code == 422
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM closing_review_tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM closing_review_messages").fetchone()[0] == 0
