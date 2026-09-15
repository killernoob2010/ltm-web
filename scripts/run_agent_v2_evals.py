"""Run definition checks or a fixed, synthetic offline behavior suite."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.trading_agent import quality

EVAL_DIR = ROOT / "evals" / "trading_agent_v2"
MANIFEST_PATH = EVAL_DIR / "offline_behavior_manifest.json"
MIGRATION_CASES_PATH = EVAL_DIR / "migration_cases.jsonl"
TOOL_NAMES = {
    "describe_capabilities", "query_trade_facts", "query_close_facts", "query_positions",
    "summarize_positions", "summarize_facts", "read_result_page", "compare_results",
    "get_position_risk", "run_scenario", "explain_evidence", "search_public", "read_public",
    "describe_dataset", "query_dataset", "summarize_dataset", "compare_dataset",
    "relate_datasets", "get_optimal_warrant",
}
CAPABILITY_NAMES = set(json.loads((EVAL_DIR / "capabilities.json").read_text())["capabilities"])
REQUIRED_FIELDS = {
    "id", "capabilities", "question", "fixture", "required_evidence",
    "allowed_tools", "forbidden_behaviors", "oracle", "split",
}
MIGRATION_REQUIRED_FIELDS = {
    "id", "origin", "source_task_ref", "turns", "snapshot_id", "clock",
    "expected_requirements", "oracle", "forbidden", "required_evidence",
    "expected_answer_state", "live_required", "split",
}
MIGRATION_ORIGINS = {"history", "synthetic", "holdout"}
MIGRATION_SPLITS = {"regression", "holdout"}
MIGRATION_ANSWER_STATES = {"complete", "partial", "failed", "needs_clarification"}


def load_cases(suite):
    path = EVAL_DIR / ("cases.jsonl" if suite == "regression" else "holdout.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_migration_cases(path=MIGRATION_CASES_PATH):
    cases = []
    for line_number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid migration case JSON on line {line_number}") from exc
        cases.append(case)
    return cases


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def validate_case_definition(case):
    """Validate the test-case manifest only; do not score an Agent answer."""
    errors = []
    if not isinstance(case, dict):
        return {"id": "", "definition_pass": False, "errors": ["invalid_case"]}
    missing = sorted(REQUIRED_FIELDS - set(case))
    errors.extend(f"missing_field:{field}" for field in missing)
    if "id" in case and not _nonempty_string(case["id"]):
        errors.append("invalid_id")
    if "question" in case and not _nonempty_string(case["question"]):
        errors.append("invalid_question")
    if "fixture" in case and not _nonempty_string(case["fixture"]):
        errors.append("invalid_fixture")
    capabilities = case.get("capabilities")
    if "capabilities" in case and (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(_nonempty_string(item) for item in capabilities)
        or not set(capabilities).issubset(CAPABILITY_NAMES)
    ):
        errors.append("invalid_capabilities")
    for field in ("required_evidence", "allowed_tools", "forbidden_behaviors"):
        value = case.get(field)
        if field in case and (not isinstance(value, list) or
                              (field == "forbidden_behaviors" and not value)):
            errors.append(f"invalid_{field}")
    if isinstance(case.get("allowed_tools"), list) and not set(case["allowed_tools"]).issubset(TOOL_NAMES):
        errors.append("unknown_tool")
    if "oracle" in case and (not isinstance(case["oracle"], dict) or not case["oracle"]):
        errors.append("invalid_oracle")
    if "split" in case and case["split"] not in {"regression", "holdout"}:
        errors.append("invalid_split")
    return {
        "id": str(case.get("id") or ""),
        "definition_pass": not errors,
        "errors": errors,
    }


def validate_migration_case(case):
    """Validate a migration case definition without evaluating an answer."""
    if not isinstance(case, dict):
        return ["invalid_case"]

    errors = [f"missing:{field}" for field in sorted(MIGRATION_REQUIRED_FIELDS - set(case))]
    if "id" in case and (not isinstance(case["id"], str) or not case["id"].strip()):
        errors.append("invalid_id")
    if "origin" in case and (not isinstance(case["origin"], str) or case["origin"] not in MIGRATION_ORIGINS):
        errors.append("invalid_origin")
    if "split" in case and (not isinstance(case["split"], str) or case["split"] not in MIGRATION_SPLITS):
        errors.append("invalid_split")
    if "turns" in case and (
        not isinstance(case["turns"], list)
        or not case["turns"]
        or not all(isinstance(turn, str) and turn.strip() for turn in case["turns"])
    ):
        errors.append("invalid_turns")
    if "snapshot_id" in case and (
        not isinstance(case["snapshot_id"], str) or not case["snapshot_id"].strip()
    ):
        errors.append("invalid_snapshot_id")
    if "source_task_ref" in case and case["source_task_ref"] is not None and (
        not isinstance(case["source_task_ref"], str) or not case["source_task_ref"].strip()
    ):
        errors.append("invalid_source_task_ref")
    for field in ("expected_requirements", "forbidden", "required_evidence"):
        if field in case and (
            not isinstance(case[field], list)
            or not case[field]
            or not all(isinstance(item, str) and item.strip() for item in case[field])
        ):
            errors.append(f"invalid:{field}")
    if "oracle" in case:
        if not isinstance(case["oracle"], dict):
            errors.append("invalid_oracle")
        elif not case["oracle"]:
            errors.append("empty_oracle")
    if "clock" in case:
        clock = case["clock"]
        if not isinstance(clock, str) or not clock.strip():
            errors.append("invalid_clock")
        else:
            try:
                parsed_clock = datetime.fromisoformat(clock)
            except ValueError:
                errors.append("invalid_clock")
            else:
                if parsed_clock.tzinfo is None or parsed_clock.utcoffset() is None:
                    errors.append("clock_missing_timezone")
    if "expected_answer_state" in case and (not isinstance(case["expected_answer_state"], str)
                                           or case["expected_answer_state"] not in MIGRATION_ANSWER_STATES):
        errors.append("invalid_answer_state")
    if "live_required" in case and not isinstance(case["live_required"], bool):
        errors.append("invalid_live_required")
    return errors


def validate_migration_manifest(cases):
    errors = []
    seen_ids = set()
    for case in cases:
        errors.extend(validate_migration_case(case))
        case_id = case.get("id") if isinstance(case, dict) else None
        if isinstance(case_id, str) and case_id.strip():
            if case_id in seen_ids:
                errors.append(f"duplicate_id:{case_id}")
            seen_ids.add(case_id)
    return errors


def migration_definition_output(path=MIGRATION_CASES_PATH):
    cases = load_migration_cases(path)
    results = [
        {
            "id": str(case.get("id") or "") if isinstance(case, dict) else "",
            "definition_pass": not validate_migration_case(case),
            "errors": validate_migration_case(case),
        }
        for case in cases
    ]
    manifest_errors = validate_migration_manifest(cases)
    return {
        "mode": "migration-definition",
        "summary": {
            "count": len(cases),
            "definition_failures": sum(not item["definition_pass"] for item in results),
            "definition_pass": bool(cases) and not manifest_errors,
            "manifest_errors": manifest_errors,
        },
        "cases": results,
        "business_pass": False,
        "real_model_evaluated": False,
        "release_readiness": "not_evaluated",
    }


def summarize_definitions(results):
    return {
        "count": len(results),
        "definition_failures": sum(not item.get("definition_pass", False) for item in results),
        "definitions_pass": bool(results) and all(item.get("definition_pass") is True for item in results),
    }


def _manifest_cases(manifest_path=MANIFEST_PATH):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get("version") != 1 or not isinstance(manifest.get("cases"), list):
        raise ValueError("offline behavior manifest is invalid")
    cases = manifest["cases"]
    if not cases or any(
        not isinstance(item, dict)
        or not _nonempty_string(item.get("id"))
        or not _nonempty_string(item.get("nodeid"))
        or not item["nodeid"].startswith("tests/agent_v2/")
        for item in cases
    ):
        raise ValueError("offline behavior manifest contains invalid nodeid")
    nodeids = [item["nodeid"] for item in cases]
    if len(nodeids) != len(set(nodeids)):
        raise ValueError("offline behavior manifest contains duplicate nodeid")
    return cases


def _junit_statuses(xml_path, cases):
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return {item["id"]: "error" for item in cases}
    by_name = {}
    for testcase in root.iter("testcase"):
        name = testcase.attrib.get("name")
        if not name or name in by_name:
            if name:
                by_name[name] = "error"
            continue
        if testcase.find("error") is not None:
            status = "error"
        elif testcase.find("failure") is not None:
            status = "failed"
        elif testcase.find("skipped") is not None:
            status = "not_run"
        else:
            status = "passed"
        by_name[name] = status
    statuses = {}
    for item in cases:
        name = item["nodeid"].rsplit("::", 1)[-1]
        statuses[item["id"]] = by_name.get(name, "error")
    return statuses


def run_offline_behavior(*, manifest_path=MANIFEST_PATH, runner=subprocess.run, timeout_seconds=120):
    """Execute only the checked-in synthetic pytest nodeids; never contacts a model."""
    cases = _manifest_cases(manifest_path)
    nodeids = [item["nodeid"] for item in cases]
    with tempfile.TemporaryDirectory(prefix="agent-v2-eval-") as temp_dir:
        xml_path = Path(temp_dir) / "results.xml"
        command = [
            sys.executable, "-m", "pytest", *nodeids, "-q", f"--junitxml={xml_path}",
        ]
        try:
            completed = runner(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            statuses = {item["id"]: "error" for item in cases}
            return {
                "mode": "offline-behavior",
                "model_source": "scripted_or_none",
                "data_source": "synthetic",
                "executed_count": 0,
                "passed_count": 0,
                "failed_count": 0,
                "not_run_count": len(cases),
                "cases": [{"id": item["id"], "status": statuses[item["id"]]} for item in cases],
                "offline_behavior_pass": False,
                "real_model_evaluated": False,
                "release_readiness": "not_evaluated",
            }
        statuses = _junit_statuses(xml_path, cases)
    outputs = [{"id": item["id"], "status": statuses[item["id"]]} for item in cases]
    passed = sum(item["status"] == "passed" for item in outputs)
    failed = sum(item["status"] == "failed" for item in outputs)
    not_run = sum(item["status"] != "passed" and item["status"] != "failed" for item in outputs)
    return {
        "mode": "offline-behavior",
        "model_source": "scripted_or_none",
        "data_source": "synthetic",
        "executed_count": len(cases) - not_run,
        "passed_count": passed,
        "failed_count": failed,
        "not_run_count": not_run,
        "cases": outputs,
        "offline_behavior_pass": completed.returncode == 0 and passed == len(cases),
        "real_model_evaluated": False,
        "release_readiness": "not_evaluated",
    }


def _definition_output(suite):
    results = [validate_case_definition(case) for case in load_cases(suite)]
    return {
        "mode": "definition",
        "suite": suite,
        "summary": summarize_definitions(results),
        "cases": results,
    }


def persist_evaluation(output, *, suite: str, execution_source: str, environment: str,
                       idempotency_key: str | None = None) -> dict:
    """Persist an executed local/offline batch; never marks a live model pass."""
    if execution_source == "offline":
        manifest = {item["id"]: item for item in _manifest_cases()}
        cases = [manifest.get(item["id"], {"id": item["id"]}) for item in output.get("cases", [])]
    else:
        cases = load_cases(suite)
    key = idempotency_key or f"{execution_source}:{suite}:{output.get('mode')}"
    batch_id = quality.create_evaluation_batch(
        suite="offline_behavior" if execution_source == "offline" else suite,
        execution_source=execution_source,
        environment=environment,
        cases=cases,
        idempotency_key=key,
        metadata={"runner": "scripts/run_agent_v2_evals.py", "mode": output.get("mode")},
    )
    results = {item["id"]: item for item in output.get("cases", [])}
    for case in cases:
        result = results.get(case["id"], {})
        if execution_source == "deterministic":
            status = "passed" if result.get("definition_pass") else "failed"
            score = {"definition_pass": bool(result.get("definition_pass")), "errors": result.get("errors", [])}
        else:
            raw_status = result.get("status", "not_run")
            status = raw_status if raw_status in {"passed", "failed", "blocked", "needs_review", "not_run"} else "not_run"
            score = {"runner_status": raw_status}
        quality.record_evaluation_case(batch_id, case["id"], status=status, score=score)
    return quality.finish_evaluation_batch(batch_id, metadata={"runner": "scripts/run_agent_v2_evals.py"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["definition", "deterministic", "migration-definition", "offline-behavior", "live"],
        default="definition",
    )
    parser.add_argument("--suite", choices=["regression", "holdout"], default="regression")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--persist", action="store_true", help="将本次定义/离线执行记录写入 Evaluation 附表")
    parser.add_argument("--environment", default="local")
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    if args.mode == "live":
        parser.error("live mode requires the approved Staging model/tool runner; no external runner is enabled")
    if args.mode == "migration-definition" and args.persist:
        parser.error("migration-definition is read-only and cannot persist evaluation data")
    if args.mode == "migration-definition":
        output = migration_definition_output()
        success = output["summary"]["definition_pass"]
    elif args.mode in {"definition", "deterministic"}:
        output = _definition_output(args.suite)
        success = output["summary"]["definitions_pass"]
    else:
        output = run_offline_behavior()
        success = output["offline_behavior_pass"]
        if args.persist:
            output["persisted_batch"] = persist_evaluation(
                output, suite="offline_behavior", execution_source="offline",
                environment=args.environment, idempotency_key=args.idempotency_key,
            )
    if args.persist and args.mode in {"definition", "deterministic"}:
        output["persisted_batch"] = persist_evaluation(
            output, suite=args.suite, execution_source="deterministic",
            environment=args.environment, idempotency_key=args.idempotency_key,
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
