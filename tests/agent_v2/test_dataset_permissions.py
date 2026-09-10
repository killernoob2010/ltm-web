from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import db
from app.trading_agent import auth, dv_queries, store, tools
from app.trading_agent.contracts import ToolEnvelope
from app.trading_agent.dv_contracts import DatasetQuery
from app.trading_agent.schema import migrate_agent_v2_schema


@pytest.fixture
def display_only_principal(monkeypatch):
    db.init_db()
    monkeypatch.setenv("AGENT_V2_PILOT_USERNAME", "dv_only")
    with db.connect() as conn:
        user_id = conn.execute(
            "INSERT INTO users(name,username,department,password_hash,role) VALUES ('DV only','dv_only','贸易处','x','用户')"
        ).lastrowid
        for module in ("closing_review_agent", "data_visualization_chart", "data_visualization_data"):
            conn.execute(
                "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?,?,1,0)",
                (user_id, module),
            )
        conversation_id = conn.execute(
            "INSERT INTO closing_review_conversations(user_id,channel,kind,title) VALUES (?,'web','v2_conversation','DV')",
            (user_id,),
        ).lastrowid
        migrate_agent_v2_schema(conn)
    task_id = store.enqueue({"id": user_id}, conversation_id, str(uuid4()), "查库存", "web")
    assert store.claim_next("dv-worker") == task_id
    return user_id, conversation_id, store.principal_for_task(task_id)


def _envelope(kind):
    return ToolEnvelope(
        status="complete",
        captured_at=datetime.now(timezone.utc),
        calculation_version="dv-test",
        payload={"kind": kind},
    )


def test_data_visualization_only_principal_has_empty_trading_scope(display_only_principal):
    _, _, principal = display_only_principal
    assert principal.account_ids == ()
    auth.authorize(principal, "data_visualization.display")
    with pytest.raises(HTTPException) as error:
        auth.authorize(principal, "trading.facts")
    assert error.value.status_code == 403


def test_capability_catalog_hides_unrelated_tools_for_display_only_principal(display_only_principal):
    _, _, principal = display_only_principal
    envelope = tools.dispatch(principal, "describe_capabilities")
    assert "query_dataset" in envelope.payload["tools"]
    assert "query_positions" not in envelope.payload["tools"]
    assert "query_market_series" not in envelope.payload["tools"]


def test_dataset_snapshot_can_be_saved_and_read_without_trading_permission(display_only_principal):
    user_id, conversation_id, principal = display_only_principal
    ref = store.save_result(
        principal,
        _envelope("dataset_rows"),
        [{"row_ref": "r1", "value": "90"}],
        kind="dataset_rows",
        required_resources=["data_visualization.display"],
        account_scope=[],
    )
    saved = store.load_result(principal, ref)
    assert saved.rows == [{"row_ref": "r1", "value": "90"}]
    assert saved.envelope.payload["account_scope"] == []
    assert store.load_result_for_view(user_id, conversation_id, ref).rows == saved.rows


def test_revoking_one_parent_resource_blocks_derived_result_and_view(display_only_principal):
    user_id, conversation_id, principal = display_only_principal
    parent = store.save_result(
        principal,
        _envelope("dataset_rows"),
        [{"row_ref": "r1", "value": "90"}],
        kind="dataset_rows",
        required_resources=["data_visualization.display"],
        account_scope=[],
    )
    child = store.save_result(
        principal,
        _envelope("dataset_summary"),
        [{"row_ref": "s1", "value": "90"}],
        kind="dataset_summary",
        input_refs=[parent],
        required_resources=["data_visualization.display", "data_visualization.data"],
        account_scope=[],
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE module_permissions SET can_view=0 WHERE user_id=? AND module_code='data_visualization_data'",
            (user_id,),
        )
    with pytest.raises(HTTPException) as error:
        store.load_result(principal, child)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        store.load_result_for_view(user_id, conversation_id, child)


def test_dataset_default_projection_does_not_include_source_metadata(display_only_principal, monkeypatch):
    _, _, principal = display_only_principal
    monkeypatch.setattr(dv_queries, "_query_rows", lambda args: [{
        "row_ref": "r1", "observation_date": "2026-09-01", "port": "青岛港",
        "value": "100", "unit": "万吨", "source_file": "private.xlsx", "source_row": 17,
        "package_id": "secret-package",
    }])
    envelope = dv_queries.query_dataset(
        principal,
        DatasetQuery(dataset="port_inventory", mode="latest", filters={}),
    )
    row = envelope.payload["preview"][0]
    assert row["value"] == "100"
    assert "source_file" not in row
    assert "source_row" not in row
    assert "package_id" not in row
