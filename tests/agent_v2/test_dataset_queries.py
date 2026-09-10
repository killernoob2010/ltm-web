from uuid import uuid4

import pytest

from app import db
from app.trading_agent import dv_queries, store
from app.trading_agent.dv_contracts import DatasetQuery
from app.trading_agent.schema import migrate_agent_v2_schema


@pytest.fixture
def dv_query_context(monkeypatch):
    db.init_db()
    monkeypatch.setenv("AGENT_V2_PILOT_USERNAME", "dv_query_user")
    with db.connect() as conn:
        user_id = conn.execute(
            "INSERT INTO users(name,username,department,password_hash,role) VALUES ('DV query','dv_query_user','贸易处','x','用户')"
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
        conn.execute(
            "INSERT INTO dv_source_packages(package_id,structure_version,parser_version,mapping_version,status) VALUES ('pkg-active','v2','p','m','activated')"
        )
        conn.execute(
            "INSERT INTO dv_source_packages(package_id,structure_version,parser_version,mapping_version,status) VALUES ('pkg-prepared','v2','p','m','prepared')"
        )
        for package_id, value in (("pkg-active", 90), ("pkg-prepared", 999)):
            conn.execute(
                """INSERT INTO dv_port_inventory_facts
                   (package_id,observed_date,week_start,sample_name,port_name,region,scope_type,
                    raw_product,product,category,source_country,mainstream_status,value,value_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (package_id, "2026-09-07", "2026-09-07", "样本", "日照", "山东", "total",
                 "PB粉", "PB粉", "粉矿", "澳大利亚", "主流", value, "有效"),
            )
        conn.execute(
            "INSERT INTO dv_integration_batches(file_names,status,point_count) VALUES ('legacy.xlsx','committed',1)"
        )
        batch_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO dv_integrated_points
               (batch_id,week_start,week_end,business_year,business_week,week_label,display_date,
                metric_type,source_country,product,category,mainstream_status,value,validation_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (batch_id, "2026-09-07", "2026-09-13", 2026, 37, "W37", "2026-09-13",
             "inventory", "澳大利亚", "PB粉", "粉矿", "主流", 90, "ok"),
        )
        migrate_agent_v2_schema(conn)
    task_id = store.enqueue({"id": user_id}, conversation_id, str(uuid4()), "查PB粉库存", "web")
    assert store.claim_next("dv-query-worker") == task_id
    return user_id, conversation_id, store.principal_for_task(task_id)


def test_query_dataset_reads_only_activated_package_and_saves_snapshot(dv_query_context):
    _, _, principal = dv_query_context
    result = dv_queries.query_dataset(
        principal,
        DatasetQuery(
            dataset="port_inventory",
            mode="range",
            start_date="2026-09-01",
            end_date="2026-09-14",
            filters={"ports": ["日照"], "products": ["PB粉"]},
            fields=["observation_date", "port", "product", "value", "value_state", "package_id"],
        ),
    )
    assert result.status == "complete"
    assert result.payload["row_count"] == 1
    assert result.payload["preview"][0]["value"] == 90
    assert result.payload["preview"][0]["package_id"] == "pkg-active"
    assert result.result_ref is not None


def test_query_dataset_latest_keeps_current_and_strict_previous_week(dv_query_context):
    _, _, principal = dv_query_context
    result = dv_queries.query_dataset(
        principal,
        DatasetQuery(dataset="spot_series", filters={"metric": "inventory"}),
    )
    assert result.status == "complete"
    assert {row["observation_date"] for row in result.payload["preview"]} == {"2026-09-13"}


def test_query_dataset_returns_controlled_limit_without_partial_claim(dv_query_context, monkeypatch):
    _, _, principal = dv_query_context
    rows = [
        {
            "source_row_id": index,
            "observation_date": f"2026-09-{(index % 28) + 1:02d}",
            "period_start": "2026-09-01",
            "port": f"港{index}",
            "product": "PB粉",
            "value": index,
            "value_state_source": "有效",
        }
        for index in range(20001)
    ]
    monkeypatch.setattr(dv_queries, "_readonly_fetch", lambda *args: rows)
    result = dv_queries.query_dataset(
        principal,
        DatasetQuery(dataset="port_inventory", mode="range", start_date="2026-09-01", end_date="2026-09-30"),
    )
    assert result.status == "limit_exceeded"
    assert result.result_ref is None
    assert result.missing[0]["code"] == "limit_exceeded"


def test_query_adapter_does_not_invoke_import_or_sync_paths(dv_query_context, monkeypatch):
    _, _, principal = dv_query_context
    import app.data_visualization as data_visualization

    monkeypatch.setattr(data_visualization, "_sync", lambda *args, **kwargs: pytest.fail("sync must not run"), raising=False)
    result = dv_queries.describe_dataset(principal, "port_inventory")
    assert result.status == "complete"
