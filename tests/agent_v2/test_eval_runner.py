from scripts.run_agent_v2_evals import summarize_scores, load_cases, evaluate_case


def test_critical_failure_cannot_be_averaged_away():
    scores = [{"hard_pass": True, "soft_score": 10, "soft_dimensions": {"a": 2}}] * 39
    scores += [{"hard_pass": False, "soft_score": 10, "soft_dimensions": {"a": 2}}]
    assert summarize_scores(scores)["release_pass"] is False


def test_missing_soft_dimension_is_a_gate_failure():
    assert summarize_scores([{
        "hard_pass": True, "soft_score": 10,
        "soft_dimensions": {"relevance": 2},
    }])["release_pass"] is False


def test_regression_and_holdout_cases_cover_general_capabilities():
    regression, holdout = load_cases("regression"), load_cases("holdout")
    assert len(regression) >= 40 and len(holdout) >= 12
    assert len({cap for case in regression for cap in case["capabilities"]}) >= 10
    assert all(evaluate_case(case)["hard_pass"] for case in regression + holdout)
