from datetime import datetime, timezone

import pytest

from app.trading_agent import tools, store
from app.trading_agent.contracts import ToolEnvelope
from test_store import queued


def test_registry_has_only_read_only_allowlisted_tools():
    names = set(tools.TOOL_SPECS)
    assert names == {
        "describe_capabilities", "query_trade_facts", "query_close_facts", "query_positions",
        "summarize_positions", "summarize_facts", "read_result_page", "compare_results",
        "get_position_risk", "run_scenario", "explain_evidence", "search_public", "read_public",
    }
    assert all("sql" not in " ".join(spec["model"].model_fields) for spec in tools.TOOL_SPECS.values())


def test_dispatch_rejects_unknown_tool_and_raw_identity(queued):
    principal = store.principal_for_task(store.claim_next("tool-worker"))
    with pytest.raises(ValueError):
        tools.dispatch(principal, "delete_everything", {})
    with pytest.raises(Exception):
        tools.dispatch(principal, "query_positions", {"user_id": 1})


def test_capability_catalog_is_authorized_and_versioned(queued):
    principal = store.principal_for_task(store.claim_next("tool-worker"))
    envelope = tools.dispatch(principal, "describe_capabilities")
    assert envelope.status == "complete"
    assert envelope.payload["defaults"]["as_of"] == "latest"
    assert "query_positions" in envelope.payload["tools"]


def test_explain_evidence_cannot_read_unknown_metric(queued):
    principal = store.principal_for_task(store.claim_next("tool-worker"))
    ref = store.save_result(principal, ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc), calculation_version="t", payload={"kind":"positions"}), [])
    with pytest.raises(ValueError):
        tools.dispatch(principal, "explain_evidence", {"result_ref": str(ref), "metric_path": "/metrics/secret"})
