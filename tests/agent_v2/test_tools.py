from datetime import datetime, timezone

import pytest

from app import db
from app.trading_agent import tools, store
from app.trading_agent.contracts import ToolEnvelope
from test_store import queued


def test_registry_has_only_read_only_allowlisted_tools():
    names = set(tools.TOOL_SPECS)
    assert names == {
        "describe_capabilities", "query_trade_facts", "query_close_facts", "query_positions",
        "query_market_series",
        "describe_dataset", "query_dataset", "summarize_dataset", "compare_dataset",
        "relate_datasets", "get_optimal_warrant",
        "summarize_positions", "summarize_facts", "read_result_page", "compare_results",
        "get_position_risk", "run_scenario", "explain_evidence", "search_public", "read_public",
    }
    assert all("sql" not in " ".join(spec["model"].model_fields) for spec in tools.TOOL_SPECS.values())


def test_agent_module_catalog_has_six_modules_and_hard_forbids_backend_data():
    catalog = tools.agent_module_catalog()
    assert set(catalog) == {
        "trade_ledger", "trading", "information_warning", "data_visualization",
        "order_finance", "backend_admin",
    }
    assert catalog["backend_admin"]["agent_access"] is False
    assert catalog["order_finance"]["connection_status"] == "not_connected"


def test_dataset_tool_schema_is_strict_and_has_no_identity_or_sql_arguments():
    schema = tools.TOOL_SPECS["query_dataset"]["model"].model_json_schema()
    assert schema["additionalProperties"] is False
    assert "user_id" not in schema["properties"]
    assert "account_ids" not in schema["properties"]
    assert "sql" not in schema["properties"]


def test_dispatch_rejects_unknown_tool_and_raw_identity(queued):
    principal = store.principal_for_task(store.claim_next("tool-worker"))
    with pytest.raises(ValueError):
        tools.dispatch(principal, "delete_everything", {})
    with pytest.raises(Exception):
        tools.dispatch(principal, "query_positions", {"user_id": 1})


def test_capability_catalog_is_authorized_and_versioned(queued):
    principal = store.principal_for_task(store.claim_next("tool-worker"))
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?, 'data_visualization_chart', 1, 0)",
            (principal.user_id,),
        )
    envelope = tools.dispatch(principal, "describe_capabilities")
    assert envelope.status == "complete"
    assert envelope.payload["defaults"]["as_of"] == "latest"
    assert "query_positions" in envelope.payload["tools"]
    assert envelope.payload["datasets"]["iron_ore_basis"]["source"] == "iron_ore_basis_results"


def test_explain_evidence_cannot_read_unknown_metric(queued):
    principal = store.principal_for_task(store.claim_next("tool-worker"))
    ref = store.save_result(principal, ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc), calculation_version="t", payload={"kind":"positions"}), [])
    with pytest.raises(ValueError):
        tools.dispatch(principal, "explain_evidence", {"result_ref": str(ref), "metric_path": "/metrics/secret"})


def test_rejected_public_query_crosses_tool_boundary_without_query_data(queued, monkeypatch):
    from app.trading_agent import research

    principal = store.principal_for_task(store.claim_next("research-rejection-worker"))

    def reject(*args, **kwargs):
        raise research.QueryRejected("private query must not cross the tool boundary")

    monkeypatch.setattr(research, "search_public", reject)
    envelope = tools.dispatch(principal, "search_public", {"public_query": "private", "freshness": "none"})

    assert envelope.status == "temporarily_unavailable"
    assert envelope.payload == {"kind": "public_query_rejected", "query_sent": False}
    assert all("private query" not in warning for warning in envelope.warnings)


def test_quote_wait_is_bounded_and_does_not_queue_more_requests(monkeypatch):
    import threading
    from app import trading_valuation
    from app.trading_agent import tools
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(requests):
        calls.append(1)
        entered.set()
        release.wait(2)
        return {}
    monkeypatch.setattr(trading_valuation, 'get_quote_snapshots', slow)
    try:
        with pytest.raises(TimeoutError):
            tools.default_quote_provider([], timeout_seconds=.02)
        assert entered.is_set()
        with pytest.raises(TimeoutError):
            tools.default_quote_provider([], timeout_seconds=.02)
        assert calls == [1]
    finally:
        release.set()


def test_default_quote_wait_covers_observed_cold_start_but_remains_bounded(monkeypatch):
    from app.trading_agent import tools
    class Future:
        def add_done_callback(self, callback):
            callback(self)
        def result(self, timeout):
            if timeout < 12:
                raise TimeoutError("cold provider needs twelve seconds")
            assert timeout <= 22
            return {"sample": "available"}
    class Executor:
        def submit(self, *args):
            return Future()
    monkeypatch.setattr(tools, "_quote_executor", Executor())
    assert tools.default_quote_provider([]) == {"sample": "available"}
