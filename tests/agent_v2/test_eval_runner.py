import json
from types import SimpleNamespace

from app import db
from app.trading_agent import quality
from app.trading_agent.schema import migrate_agent_v2_schema
from scripts.run_agent_v2_evals import (
    _junit_statuses,
    load_cases,
    run_offline_behavior,
    summarize_definitions,
    validate_case_definition,
    validate_live_receipt,
)


def test_definition_validation_does_not_score_behavior():
    case = {
        "id": "synthetic",
        "capabilities": ["intent_understanding"],
        "question": "任意问题",
        "fixture": "fixture",
        "required_evidence": [],
        "allowed_tools": ["describe_capabilities"],
        "forbidden_behaviors": ["invent_numbers"],
        "oracle": {"status": "complete"},
        "split": "regression",
    }
    result = validate_case_definition(case)
    assert result["definition_pass"] is True
    assert "soft_score" not in result
    assert "hard_pass" not in result


def test_definition_validation_reports_missing_fields_without_key_error():
    result = validate_case_definition({"id": "incomplete"})
    assert result["definition_pass"] is False
    assert "missing_field:question" in result["errors"]


def test_definition_summary_has_no_release_score():
    summary = summarize_definitions([
        {"id": "ok", "definition_pass": True, "errors": []},
        {"id": "bad", "definition_pass": False, "errors": ["missing_field:oracle"]},
    ])
    assert summary == {
        "count": 2,
        "definition_failures": 1,
        "definitions_pass": False,
    }


def test_regression_and_holdout_definitions_cover_general_capabilities():
    regression, holdout = load_cases("regression"), load_cases("holdout")
    results = [validate_case_definition(case) for case in regression + holdout]
    assert len(regression) >= 40 and len(holdout) >= 12
    assert len({cap for case in regression for cap in case["capabilities"]}) >= 10
    assert all(result["definition_pass"] for result in results)


def test_catalog_includes_planner_source_and_coverage_regressions():
    regression = {case["id"]: case for case in load_cases("regression")}
    holdout = {case["id"]: case for case in load_cases("holdout")}

    assert {"reg-50", "reg-51", "reg-52"}.issubset(regression)
    assert {"hold-16", "hold-17", "hold-18"}.issubset(holdout)
    assert "public_research_egress" in regression["reg-50"]["capabilities"]
    assert "permission_boundary" in regression["reg-51"]["capabilities"]
    assert "evidence_bound_answer" in regression["reg-52"]["capabilities"]
    assert regression["reg-50"]["oracle"]["source_mode"] == "mixed"
    assert regression["reg-51"]["oracle"]["search_requests"] == 0
    assert regression["reg-52"]["oracle"]["required_metrics"] == [
        "gross_quantity", "net_quantity", "floating_pnl"
    ]


def test_junit_statuses_mark_missing_duplicate_and_skipped_cases(tmp_path):
    xml = tmp_path / "results.xml"
    xml.write_text(
        "<testsuite>"
        '<testcase classname="test" name="passed"/>'
        '<testcase classname="test" name="skipped"><skipped/></testcase>'
        '<testcase classname="test" name="passed"/>'
        "</testsuite>"
    )
    cases = [
        {"id": "one", "nodeid": "tests/agent_v2/test.py::passed"},
        {"id": "two", "nodeid": "tests/agent_v2/test.py::skipped"},
        {"id": "three", "nodeid": "tests/agent_v2/test.py::missing"},
    ]
    assert _junit_statuses(xml, cases) == {
        "one": "error",
        "two": "not_run",
        "three": "error",
    }


def test_offline_runner_uses_fixed_nodeids_and_reports_real_subprocess_status(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "cases": [{
            "id": "one",
            "nodeid": "tests/agent_v2/test.py::one",
            "capabilities": ["budget_and_recovery"],
        }],
    }))

    def fake_runner(command, **kwargs):
        assert kwargs["shell"] is False
        assert "tests/agent_v2/test.py::one" in command
        xml_path = next(item.split("=", 1)[1] for item in command if item.startswith("--junitxml="))
        with open(xml_path, "w", encoding="utf-8") as handle:
            handle.write('<testsuite><testcase classname="test" name="one"/></testsuite>')
        return SimpleNamespace(returncode=0)

    result = run_offline_behavior(manifest_path=manifest, runner=fake_runner)
    assert result["offline_behavior_pass"] is True
    assert result["real_model_evaluated"] is False
    assert result["passed_count"] == 1


def test_offline_runner_marks_timeout_as_not_run(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "cases": [{
            "id": "one",
            "nodeid": "tests/agent_v2/test.py::one",
            "capabilities": ["budget_and_recovery"],
        }],
    }))

    def fake_runner(*args, **kwargs):
        import subprocess
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    result = run_offline_behavior(manifest_path=manifest, runner=fake_runner)
    assert result["offline_behavior_pass"] is False
    assert result["cases"] == [{"id": "one", "status": "error"}]
    assert result["not_run_count"] == 1


def test_evaluation_batch_is_idempotent_and_keeps_case_level_status(pilot):
    with db.connect() as conn:
        migrate_agent_v2_schema(conn)

    cases = [
        {"id": "synthetic-pass", "question": "权限边界", "capabilities": ["permission_boundary"]},
        {"id": "synthetic-blocked", "question": "公开研究", "capabilities": ["public_research_egress"]},
    ]
    batch_id = quality.create_evaluation_batch(
        suite="regression", execution_source="deterministic", environment="local",
        cases=cases, idempotency_key="test-eval-regression-v1",
    )
    assert quality.create_evaluation_batch(
        suite="regression", execution_source="deterministic", environment="local",
        cases=cases, idempotency_key="test-eval-regression-v1",
    ) == batch_id

    quality.record_evaluation_case(batch_id, "synthetic-pass", status="passed", score={"score": 1})
    quality.record_evaluation_case(batch_id, "synthetic-blocked", status="blocked", evidence={"reason": "not configured"})
    finished = quality.finish_evaluation_batch(batch_id)

    assert finished["status"] == "blocked"
    assert finished["passed_count"] == 1
    assert finished["blocked_count"] == 1
    assert finished["not_run_count"] == 0
    assert {item["status"] for item in finished["cases"]} == {"passed", "blocked"}

    catalog = quality.evaluation_catalog()
    assert catalog["definition"]["regression"]["batch_id"] == batch_id
    assert catalog["definition"]["regression"]["status"] == "blocked"


def test_live_batch_with_no_executed_cases_is_not_a_real_model_pass(pilot):
    with db.connect() as conn:
        migrate_agent_v2_schema(conn)
    batch_id = quality.create_evaluation_batch(
        suite="live", execution_source="live", environment="production",
        cases=[{"id": "live-not-run", "question": "只登记不执行"}],
        idempotency_key="test-live-not-run-v1",
    )
    result = quality.finish_evaluation_batch(batch_id)

    assert result["status"] == "partial"
    assert result["real_model_evaluated"] is False
    assert quality.evaluation_catalog()["live"]["real_model_evaluated"] is False


def test_migration_case_rejects_empty_oracle():
    from scripts.run_agent_v2_evals import validate_migration_case

    errors = validate_migration_case({"id": "M-X", "oracle": {}})

    assert "empty_oracle" in errors
    assert "missing:clock" in errors


def test_migration_enum_fields_reject_unhashable_values():
    from scripts.run_agent_v2_evals import validate_migration_case
    for field, error in (("split", "invalid_split"), ("origin", "invalid_origin"),
                         ("expected_answer_state", "invalid_answer_state")):
        for value in ([], {}, None, 1):
            assert error in validate_migration_case({field: value})


def test_followup_cases_have_explicit_turns_and_context():
    from scripts.run_agent_v2_evals import load_migration_cases
    cases = [case for case in load_migration_cases() if case["id"].startswith("M-C")]
    assert len(cases) == 4
    for case in cases:
        assert len(case["turns"]) >= 2
        assert case["oracle"]["initial_answer"]
        assert case["oracle"]["turn_expectations"]


def test_migration_case_rejects_clock_without_timezone():
    from scripts.run_agent_v2_evals import validate_migration_case

    errors = validate_migration_case({
        "id": "M-X",
        "origin": "synthetic",
        "source_task_ref": None,
        "split": "regression",
        "turns": ["测试"],
        "snapshot_id": "synthetic-v1",
        "clock": "2026-09-15T10:00:00",
        "expected_requirements": ["test"],
        "oracle": {"status": "partial"},
        "forbidden": ["answer_complete"],
        "required_evidence": ["observation_date"],
        "expected_answer_state": "partial",
        "live_required": False,
    })

    assert "clock_missing_timezone" in errors


def test_migration_manifest_rejects_duplicate_ids():
    from scripts.run_agent_v2_evals import validate_migration_manifest

    case = {
        "id": "M-X",
        "origin": "synthetic",
        "source_task_ref": None,
        "split": "regression",
        "turns": ["测试"],
        "snapshot_id": "synthetic-v1",
        "clock": "2026-09-15T10:00:00+08:00",
        "expected_requirements": ["test"],
        "oracle": {"status": "partial"},
        "forbidden": ["answer_complete"],
        "required_evidence": ["observation_date"],
        "expected_answer_state": "partial",
        "live_required": False,
    }

    errors = validate_migration_manifest([case, dict(case)])

    assert "duplicate_id:M-X" in errors


def test_migration_cases_have_independent_30_case_distribution():
    from scripts.run_agent_v2_evals import load_migration_cases, validate_migration_case

    cases = load_migration_cases()

    assert len(cases) == 30
    assert [case["split"] for case in cases[:24]] == ["regression"] * 24
    assert [case["split"] for case in cases[24:]] == ["holdout"] * 6
    assert {case["id"][:3] for case in cases} == {"M-I", "M-W", "M-P", "M-C", "M-F", "M-S"}
    assert all(validate_migration_case(case) == [] for case in cases)


def test_migration_definition_never_claims_business_pass():
    from scripts.run_agent_v2_evals import migration_definition_output

    output = migration_definition_output()

    assert output["mode"] == "migration-definition"
    assert output["summary"]["definition_pass"] is True
    assert output["business_pass"] is False


def test_live_receipt_import_requires_deployment_task_final_and_tool_evidence():
    errors = validate_live_receipt({"mode": "live", "status": "passed"})

    assert "missing:deployment_sha" in errors
    assert "missing:task_id" in errors
    assert "missing:final_answer_ref" in errors
    assert "missing:tool_events" in errors


def test_live_receipt_import_accepts_a_bounded_receipt_without_persisting_it():
    receipt = {
        "case_id": "C-LIVE-1",
        "mode": "live",
        "deployment_sha": "abc123",
        "task_id": 17,
        "conversation_id": 9,
        "runtime": "pydantic",
        "model_config_id": "deepseek-v4-flash-disabled-thinking",
        "snapshot_refs": ["r1"],
        "tool_events": [{"tool_name": "query_positions", "status": "complete", "result_ref": "r1"}],
        "final_answer_ref": "answer-1",
        "validation_summary": {"version": "migration-v1", "checks": {
            "scope": "passed", "time": "passed", "metrics": "passed", "evidence": "passed",
            "analysis": "passed", "presentation": "passed", "rendered_content": "passed",
        }},
        "usage": {"model_calls": 2, "tool_calls": 1, "search_calls": 0},
        "human_review": None,
        "started_at": "2026-09-15T10:00:00+08:00",
        "finished_at": "2026-09-15T10:00:02+08:00",
        "final_answer": {"delivery_status": "partial", "body_markdown": "已保留核验结果。"},
        "evidence": [{"result_ref": "r1", "source": "internal"}],
    }

    assert validate_live_receipt(receipt) == []
