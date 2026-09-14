from app.trading_agent.planning_contracts import CoverageReport, TaskPlan


def _plan(topic_action="continue", *, no_web_scope="turn"):
    return TaskPlan.model_validate({
        "objective": "只看 Put 的持仓",
        "topic_action": topic_action,
        "requirements": [{
            "id": "put_positions",
            "question": "查询 Put 持仓",
            "targets": [{
                "id": "put", "domain": "positions",
                "filters": {"contract_months": ["2701"], "option_type": "put"},
                "metrics": ["quantity"], "group_by": ["option_type"],
                "label": "2701 Put",
            }],
            "source_intent": "internal",
        }],
        "restrictions": [{"kind": "no_web", "scope": no_web_scope, "evidence": "只用内部数据"}],
        "presentation": "auto",
    })


def test_state_keeps_relevant_target_and_only_persists_conversation_restriction():
    from app.trading_agent.conversation_state import update_conversation_state

    state = update_conversation_state(
        {"topic": "2701期权", "targets": [{"id": "old", "domain": "positions"}]},
        _plan(),
        CoverageReport(items=[{"requirement_id": "put_positions", "status": "answered"}], complete=True),
        ["result-1"],
        source_task_id=42,
    )

    assert state.topic == "只看 Put 的持仓"
    assert state.targets[0]["filters"]["option_type"] == "put"
    assert state.restrictions == []
    assert state.result_refs == ["result-1"]
    assert state.source_task_id == 42


def test_new_topic_drops_previous_filters_and_conversation_history_extracts_latest_state():
    from app.trading_agent.conversation_state import extract_conversation_state, update_conversation_state

    state = update_conversation_state(
        {"topic": "旧持仓", "targets": [{"id": "old", "filters": {"contract_months": ["2701"]}}]},
        _plan("new_topic"),
        CoverageReport(items=[{"requirement_id": "put_positions", "status": "blocked"}], complete=False),
        [],
    )
    history = [{"role": "assistant", "content": "conversation_state={\"topic\":\"旧\"}"}, {
        "role": "assistant", "content": "conversation_state=" + state.model_dump_json(),
    }]

    extracted = extract_conversation_state(history)

    assert extracted["topic"] == state.topic
    assert extracted["targets"][0]["filters"]["contract_months"] == ["2701"]
    assert extracted["restrictions"] == []
