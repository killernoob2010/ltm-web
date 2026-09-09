"""Run definition checks or a fixed, synthetic offline behavior suite."""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "evals" / "trading_agent_v2"
FIXTURE_PATH = EVAL_DIR / "fixtures.json"
MANIFEST_PATH = EVAL_DIR / "offline_behavior_manifest.json"
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
TOOL_NAMES = {
    "describe_capabilities", "query_trade_facts", "query_close_facts", "query_positions",
    "summarize_positions", "summarize_facts", "read_result_page", "compare_results",
    "get_position_risk", "run_scenario", "explain_evidence", "search_public", "read_public",
}
CAPABILITY_NAMES = set(json.loads((EVAL_DIR / "capabilities.json").read_text())["capabilities"])
REQUIRED_FIELDS = {
    "id", "capabilities", "question", "fixture", "required_evidence",
    "allowed_tools", "forbidden_behaviors", "oracle", "split",
}
FIXTURE_KINDS = {
    "positions_snapshot",
    "pnl_missing_quote",
    "historical_no_quote",
    "fact_inference",
    "answer_repair",
}


def load_cases(suite):
    path = EVAL_DIR / ("cases.jsonl" if suite == "regression" else "holdout.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


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


def summarize_definitions(results):
    return {
        "count": len(results),
        "definition_failures": sum(not item.get("definition_pass", False) for item in results),
        "definitions_pass": bool(results) and all(item.get("definition_pass") is True for item in results),
    }


def validate_fixture_definition(fixture):
    """Validate a synthetic fixture without executing model or network code."""
    errors = []
    if not isinstance(fixture, dict):
        return {"id": "", "definition_pass": False, "errors": ["invalid_fixture"]}
    required = {"id", "kind", "questions", "input", "oracle"}
    errors.extend(f"missing_field:{field}" for field in sorted(required - set(fixture)))
    if "id" in fixture and not _nonempty_string(fixture["id"]):
        errors.append("invalid_id")
    if "kind" in fixture and fixture.get("kind") not in FIXTURE_KINDS:
        errors.append("invalid_kind")
    questions = fixture.get("questions")
    if "questions" in fixture and (
        not isinstance(questions, list)
        or not questions
        or not all(_nonempty_string(item) for item in questions)
    ):
        errors.append("invalid_questions")
    for field in ("input", "oracle"):
        if field in fixture and (not isinstance(fixture[field], dict) or not fixture[field]):
            errors.append(f"invalid_{field}")
    return {
        "id": str(fixture.get("id") or ""),
        "definition_pass": not errors,
        "errors": errors,
    }


def load_fixtures(path=FIXTURE_PATH):
    """Load only checked-in synthetic fixtures; no external data is permitted."""
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    if source.get("version") != 1 or not isinstance(source.get("cases"), list):
        raise ValueError("fixture manifest is invalid")
    fixtures = source["cases"]
    ids = [item.get("id") if isinstance(item, dict) else None for item in fixtures]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate fixture id")
    results = [validate_fixture_definition(item) for item in fixtures]
    invalid = [item for item in results if not item["definition_pass"]]
    if invalid:
        raise ValueError(f"invalid fixture definition: {invalid[0]['id']}")
    return fixtures


def _decimal_text(value):
    return str(Decimal(str(value)))


def _replay_positions_snapshot(fixture):
    rows = fixture["input"]["rows"]
    total_quantity = sum((Decimal(str(row["quantity"])) for row in rows), Decimal(0))
    return {
        "row_count": len(rows),
        "total_quantity": str(total_quantity),
        "status": "complete",
    }


def _replay_pnl_missing_quote(fixture):
    from app.trading_valuation import calculate_live_position_floating_pnl

    rows = fixture["input"]["rows"]
    quotes = fixture["input"].get("quotes", {})
    values = []
    for row in rows:
        quote = quotes.get(row["contract"], {})
        if quote.get("last_price") is None or quote.get("multiplier") is None:
            continue
        values.append(calculate_live_position_floating_pnl(
            open_price=float(row["average_price"]),
            market_price=float(quote["last_price"]),
            direction=row["direction"],
            remaining_quantity=float(row["quantity"]),
            multiplier=float(quote["multiplier"]),
        ))
    return {
        "status": "partial" if len(values) < len(rows) else "complete",
        "floating_pnl": _decimal_text(sum(values, 0)) if values else None,
        "covered_rows": len(values),
        "eligible_rows": len(rows),
    }


def _replay_historical_no_quote(fixture):
    query = fixture["input"].get("as_of", {})
    if query.get("mode") != "settlement_date" or not query.get("date"):
        raise ValueError("historical fixture requires settlement_date")
    return {
        "status": "partial",
        "floating_pnl": None,
        "valuation_basis": "historical_unavailable",
        "data_as_of": None,
    }


def _replay_fact_inference(fixture):
    from app.trading_agent.answer import parse_answer

    draft = parse_answer(fixture["input"]["draft"])
    fact_refs = [set(item.evidence_refs) for item in draft.paragraphs if item.kind == "fact"]
    inference_refs = [set(item.evidence_refs) for item in draft.paragraphs if item.kind == "inference"]
    return {
        "status": draft.status,
        "fact_paragraph_count": len(fact_refs),
        "inference_paragraph_count": len(inference_refs),
        "shared_evidence_ref": bool(fact_refs and inference_refs and fact_refs[0] & inference_refs[0]),
    }


def _replay_answer_repair(fixture):
    from app.trading_agent.answer import AnswerValidationError, parse_answer

    initial_error = None
    try:
        parse_answer(fixture["input"]["invalid_draft"])
    except AnswerValidationError as exc:
        initial_error = exc.issues[0].code if exc.issues else "invalid_value"
    repaired = parse_answer(fixture["input"]["repaired_draft"])
    return {
        "initial_error": initial_error,
        "repair_count": 1 if initial_error else 0,
        "final_status": repaired.status,
    }


def replay_fixture(fixture):
    """Replay one fixture and compare its deterministic result with its oracle."""
    definition = validate_fixture_definition(fixture)
    if not definition["definition_pass"]:
        return {"id": definition["id"], "status": "failed", "oracle": {}, "errors": definition["errors"]}
    try:
        replayers = {
            "positions_snapshot": _replay_positions_snapshot,
            "pnl_missing_quote": _replay_pnl_missing_quote,
            "historical_no_quote": _replay_historical_no_quote,
            "fact_inference": _replay_fact_inference,
            "answer_repair": _replay_answer_repair,
        }
        actual = replayers[fixture["kind"]](fixture)
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        return {"id": fixture["id"], "status": "failed", "oracle": {}, "errors": [type(exc).__name__]}
    errors = [key for key, expected in fixture["oracle"].items() if actual.get(key) != expected]
    return {
        "id": fixture["id"],
        "status": "passed" if not errors else "failed",
        "oracle": actual,
        "errors": errors,
    }


def run_fixture_replay(*, fixture_path=FIXTURE_PATH):
    """Run the fixed synthetic fixture suite; never calls a model or external provider."""
    results = [replay_fixture(item) for item in load_fixtures(fixture_path)]
    passed = sum(item["status"] == "passed" for item in results)
    return {
        "mode": "fixture-replay",
        "data_source": "synthetic",
        "executed_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "cases": results,
        "fixture_replay_pass": bool(results) and passed == len(results),
        "real_model_evaluated": False,
        "release_readiness": "not_evaluated",
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
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["definition", "deterministic", "fixture-replay", "offline-behavior", "live"],
        default="definition",
    )
    parser.add_argument("--suite", choices=["regression", "holdout"], default="regression")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "live":
        parser.error("live mode requires the approved Staging model/tool runner; no external runner is enabled")
    if args.mode in {"definition", "deterministic"}:
        output = _definition_output(args.suite)
        success = output["summary"]["definitions_pass"]
    elif args.mode == "fixture-replay":
        output = run_fixture_replay()
        success = output["fixture_replay_pass"]
    else:
        output = run_offline_behavior()
        success = output["offline_behavior_pass"]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
