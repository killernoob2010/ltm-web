from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "sync_spot_ledger_sales_types.py"


def _load_bridge():
    assert SCRIPT_PATH.exists(), "销售类型贸易系统桥接脚本尚未实现"
    spec = importlib.util.spec_from_file_location("sync_spot_ledger_sales_types", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_build_sales_type_backfill_plan_uses_detail_id_and_complete_source_text():
    bridge = _load_bridge()

    result = bridge.build_sales_type_backfill_plan(
        [
            {"source_detail_id": "D1", "D": "贸易-港口现货-市场加价-B07"},
            {"source_detail_id": "D2", "D": "B06"},
            {"source_detail_id": "D3", "D": "贸易-落地-固定价-B05"},
        ],
        [
            {"record_id": "spot:D1", "source_detail_id": "D1", "D": "B06"},
            {"record_id": "spot:D2", "source_detail_id": "D2", "D": "B06"},
        ],
    )

    assert result["source_rows"] == 3
    assert result["invalid"] == 1
    assert result["unmatched"] == 1
    assert result["to_update"] == 1
    assert result["plans"] == [
        {
            "record_id": "spot:D1",
            "source_detail_id": "D1",
            "business_category": "贸易-港口现货-市场加价-B07",
            "expected_value": "B06",
        }
    ]


def test_build_sales_type_backfill_plan_does_not_convert_codes():
    bridge = _load_bridge()

    result = bridge.build_sales_type_backfill_plan(
        [{"source_detail_id": "D1", "D": "B06"}],
        [{"record_id": "spot:D1", "source_detail_id": "D1", "D": "B07"}],
    )

    assert result["invalid"] == 1
    assert result["to_update"] == 0
    assert result["plans"] == []
