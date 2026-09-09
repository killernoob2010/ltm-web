import json
from types import SimpleNamespace

from scripts.run_agent_v2_evals import (
    _junit_statuses,
    load_cases,
    run_offline_behavior,
    summarize_definitions,
    validate_case_definition,
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
