import json

import pytest

from scripts.run_agent_v2_evals import (
    FIXTURE_PATH,
    load_fixtures,
    replay_fixture,
    run_fixture_replay,
    validate_fixture_definition,
)


def test_fixture_manifest_has_five_frozen_replayable_cases():
    fixtures = load_fixtures()
    assert len(fixtures) == 5
    assert {item["id"] for item in fixtures} == {
        "fixture-positions-count",
        "fixture-pnl-missing-quote",
        "fixture-historical-no-quote",
        "fixture-fact-inference",
        "fixture-answer-repair",
    }
    assert all(validate_fixture_definition(item)["definition_pass"] for item in fixtures)


def test_fixture_replay_matches_frozen_oracles():
    result = run_fixture_replay()
    assert result["fixture_replay_pass"] is True
    assert result["real_model_evaluated"] is False
    assert result["data_source"] == "synthetic"
    assert result["passed_count"] == 5
    assert all(item["status"] == "passed" for item in result["cases"])


def test_position_count_fixture_keeps_three_question_paraphrases():
    fixture = next(item for item in load_fixtures() if item["id"] == "fixture-positions-count")
    assert len(fixture["questions"]) == 3
    assert len({question.strip() for question in fixture["questions"]}) == 3
    replay = replay_fixture(fixture)
    assert replay["oracle"] == {"row_count": 3, "total_quantity": "7", "status": "complete"}


def test_replay_does_not_accept_a_tampered_oracle():
    fixture = next(item for item in load_fixtures() if item["id"] == "fixture-positions-count")
    tampered = json.loads(json.dumps(fixture))
    tampered["oracle"]["total_quantity"] = "999"
    result = replay_fixture(tampered)
    assert result["status"] == "failed"
    assert "total_quantity" in result["errors"]


def test_fixture_loader_rejects_duplicate_ids(tmp_path):
    source = json.loads(FIXTURE_PATH.read_text())
    source["cases"].append(json.loads(json.dumps(source["cases"][0])))
    manifest = tmp_path / "fixtures.json"
    manifest.write_text(json.dumps(source, ensure_ascii=False))
    with pytest.raises(ValueError, match="duplicate fixture id"):
        load_fixtures(manifest)
