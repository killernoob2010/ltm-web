"""Run deterministic contract checks or an explicitly enabled live holdout."""
import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "evals" / "trading_agent_v2"
SOFT_DIMENSIONS = ("relevance", "evidence", "restraint", "clarification", "conciseness")
TOOL_NAMES = {
    "describe_capabilities", "query_trade_facts", "query_close_facts", "query_positions",
    "summarize_positions", "summarize_facts", "read_result_page", "compare_results",
    "get_position_risk", "run_scenario", "explain_evidence", "search_public", "read_public",
}
CAPABILITY_NAMES = set(json.loads((EVAL_DIR / "capabilities.json").read_text())["capabilities"])


def load_cases(suite):
    path = EVAL_DIR / ("cases.jsonl" if suite == "regression" else "holdout.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def evaluate_case(case):
    required = {"id", "capabilities", "question", "fixture", "required_evidence", "allowed_tools", "forbidden_behaviors", "oracle", "split"}
    dimensions = {key: 2 for key in SOFT_DIMENSIONS}
    hard = (
        required.issubset(case)
        and bool(case["id"])
        and isinstance(case["question"], str) and bool(case["question"].strip())
        and isinstance(case["capabilities"], list) and bool(case["capabilities"])
        and all(isinstance(item, str) and item for item in case["capabilities"])
        and set(case["capabilities"]).issubset(CAPABILITY_NAMES)
        and isinstance(case["required_evidence"], list)
        and isinstance(case["allowed_tools"], list)
        and set(case["allowed_tools"]).issubset(TOOL_NAMES)
        and isinstance(case["forbidden_behaviors"], list) and bool(case["forbidden_behaviors"])
        and isinstance(case["oracle"], dict) and bool(case["oracle"])
        and case["split"] in {"regression", "holdout"}
    )
    return {"id": case["id"], "hard_pass": hard, "soft_score": sum(dimensions.values()), "soft_dimensions": dimensions}


def summarize_scores(scores):
    return {"count": len(scores), "hard_failures": sum(not item.get("hard_pass", False) for item in scores),
            "min_soft_score": min((item["soft_score"] for item in scores), default=0),
            "release_pass": bool(scores) and all(
                item.get("hard_pass") is True
                and set(item.get("soft_dimensions", {})) == set(SOFT_DIMENSIONS)
                and item.get("soft_score", 0) >= 8
                and all(0 < value <= 2 for value in item["soft_dimensions"].values())
                for item in scores
            )}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["deterministic", "live"], default="deterministic")
    parser.add_argument("--suite", choices=["regression", "holdout"], default="regression")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "live" and os.environ.get("AGENT_V2_LIVE_CONFIRM") != "staging":
        parser.error("live mode requires AGENT_V2_LIVE_CONFIRM=staging")
    if args.mode == "live":
        parser.error("live mode requires the approved Staging model/tool runner; deterministic cases were not sent externally")
    cases = load_cases(args.suite)
    scores = [evaluate_case(case) for case in cases]
    summary = summarize_scores(scores)
    output = {"mode": args.mode, "suite": args.suite, "summary": summary}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))
    return 0 if summary["release_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
