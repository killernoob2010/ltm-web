from types import SimpleNamespace

from app.trading_agent.contracts import MetricValue, ToolEnvelope


def test_sort_whole_snapshot_numerically():
    from app.trading_agent.presentation import project_page

    rows = [{"row_ref": f"r{i:03}", "quantity": str(i)} for i in range(1, 102)]
    page = project_page(rows, ["quantity"], page=2, page_size=20,
                        sort_by="quantity", descending=True)

    assert page["pagination"]["total_rows"] == 101
    assert page["rows"][0]["quantity"] == "81"
    assert page["rows"][-1]["quantity"] == "62"


def test_sort_keeps_nulls_last_and_preserves_full_summary():
    from app.trading_agent.presentation import project_page

    rows = [
        {"row_ref": "r1", "quantity": None},
        {"row_ref": "r2", "quantity": "2"},
        {"row_ref": "r3", "quantity": "10"},
    ]
    page = project_page(rows, ["quantity"], page=1, page_size=20,
                        sort_by="quantity", descending=False)

    assert [row["quantity"] for row in page["rows"]] == ["2", "10", None]
    assert page["pagination"] == {
        "page": 1,
        "page_size": 20,
        "total_rows": 3,
        "total_pages": 1,
    }


def test_page_out_of_range_does_not_change_requested_page():
    from app.trading_agent.presentation import project_page

    page = project_page([{"row_ref": "r1", "quantity": "2"}], ["quantity"], page=3, page_size=20)

    assert page["rows"] == []
    assert page["pagination"]["page"] == 3
    assert page["pagination"]["total_rows"] == 1


def test_build_view_summary_uses_full_snapshot_not_first_page():
    from app.trading_agent.presentation import build_view

    rows = [
        {"row_ref": f"r{i:03}", "contract": "i2609", "quantity": str(i)}
        for i in range(1, 102)
    ]
    saved = SimpleNamespace(
        rows=rows,
        envelope=ToolEnvelope(
            status="complete",
            captured_at="2026-09-10T03:22:46+00:00",
            calculation_version="presentation-test",
            payload={"kind": "positions"},
            metrics={"quantity": MetricValue(value="5151", unit="手", status="complete", covered_rows=101, eligible_rows=101)},
        ),
    )
    view = build_view(
        None,
        {
            "id": "v1", "kind": "table", "result_ref": "00000000-0000-0000-0000-000000000001",
            "fields": ["contract", "quantity"], "title": "全量持仓",
        },
        SimpleNamespace(),
        saved=saved,
    )

    assert view["pagination"]["total_rows"] == 101
    assert view["summary"]["quantity"] == "5151"
    assert len(view["rows"]) == 20


def test_market_series_view_keeps_source_metrics_and_does_not_recompute():
    from app.trading_agent.presentation import build_view

    saved = SimpleNamespace(
        rows=[{
            "business_date": "2026-09-06", "port": "日照港", "product": "PB粉",
            "basis": "123.45", "futures_close": "700", "wet_spot_price": "680",
            "data_status": "有效",
        }],
        envelope=ToolEnvelope(
            status="complete",
            captured_at="2026-09-10T03:22:46+00:00",
            calculation_version="market-presentation-test",
            payload={"kind": "market_series"},
        ),
    )
    view = build_view(
        None,
        {
            "id": "v1", "kind": "table", "result_ref": "00000000-0000-0000-0000-000000000001",
            "fields": [], "title": "期现数据",
        },
        SimpleNamespace(),
        saved=saved,
    )

    assert [column["key"] for column in view["columns"]] == [
        "business_date", "port", "product", "basis", "futures_close", "wet_spot_price", "data_status",
    ]
    assert view["columns"][3]["unit"] == "元/标准化吨"
    assert view["summary"]["basis"] is None
    assert view["coverage"]["eligible_contracts"] is None
    assert view["coverage"]["covered_contracts"] is None
