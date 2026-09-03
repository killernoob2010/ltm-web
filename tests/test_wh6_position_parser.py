"""V2 full-asset fill and complete-position parser contract tests."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import struct
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account, _record, _source, _write_match
from wh6_collector.discovery import discover_wh6_sources, validate_sources
from wh6_collector.models import SourceFile
from wh6_collector.parser import (
    classify_contract,
    normalize_contract,
    parse_match_records,
    parse_position_snapshot,
)


def _position_source(path: Path) -> SourceFile:
    return SourceFile(
        path=path,
        kind="position",
        label="测试 WH6 持仓缓存",
        account_clue="宏源期货 ****1111",
        modified_ns=path.stat().st_mtime_ns,
        trading_date="2026-09-03",
    )


def _position_row(
    contract="i2607-C-750",
    *,
    direction="long",
    quantity=3,
    today_quantity=1,
    yesterday_quantity=2,
    average_price="12.50",
    exchange="DCE",
    hedge_flag="投机",
):
    return {
        "contract": contract,
        "direction": direction,
        "quantity": quantity,
        "today_quantity": today_quantity,
        "yesterday_quantity": yesterday_quantity,
        "average_price": average_price,
        "exchange": exchange,
        "hedge_flag": hedge_flag,
    }


def write_position_json(
    path: Path,
    *,
    rows=None,
    complete=True,
    snapshot_at="2026-09-03T09:05:00+08:00",
    trade_date="2026-09-03",
):
    import json

    payload = {
        "format": "wh6-position-v1",
        "version": 1,
        "complete": complete,
        "snapshot_at": snapshot_at,
        "trade_date": trade_date,
        "rows": list(rows or []),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _shifted(value: str, width: int) -> bytes:
    encoded = value.encode("gb18030")
    assert len(encoded) <= width
    raw = bytearray(width)
    raw[: len(encoded)] = bytes(min(255, byte * 2) for byte in encoded)
    return bytes(raw)


def write_position_binary(path: Path, *, rows=None, complete=True):
    rows = list(rows or [])
    header = bytearray(32)
    header[:8] = b"WH6POS1\0"
    struct.pack_into("<I", header, 8, 1)
    struct.pack_into("<I", header, 12, len(rows))
    snapshot_epoch_ms = int(datetime(2026, 9, 3, 9, 5, tzinfo=timezone.utc).timestamp() * 1000)
    struct.pack_into("<q", header, 16, snapshot_epoch_ms)
    struct.pack_into("<I", header, 24, 1 if complete else 0)
    body = bytearray()
    for row in rows:
        record = bytearray(256)
        record[0:32] = _shifted(row.get("contract", ""), 32)
        record[32:40] = _shifted(row.get("direction", ""), 8)
        struct.pack_into("<I", record, 40, int(row.get("quantity", 0)))
        struct.pack_into("<I", record, 44, int(row.get("today_quantity", 0)))
        struct.pack_into("<I", record, 48, int(row.get("yesterday_quantity", 0)))
        record[52:68] = _shifted(row.get("average_price", ""), 16)
        record[68:92] = _shifted(row.get("exchange", ""), 24)
        record[92:100] = _shifted(row.get("hedge_flag", ""), 8)
        body.extend(record)
    path.write_bytes(bytes(header) + bytes(body))


def test_contract_classifier_normalizes_future_and_call_put_options():
    assert classify_contract("i2607") == "future"
    assert classify_contract("IF2609") == "future"
    assert classify_contract("i2607-C-750") == "option"
    assert classify_contract("i2607P750") == "option"
    assert normalize_contract("i2607P750") == "i2607-p-750"
    assert classify_contract("stock-600000") is None
    assert classify_contract("p") is None


def test_full_match_parser_keeps_future_and_option_records(tmp_path):
    path = tmp_path / "20260903match.dat"
    _write_match(path, [_record(contract="i2607"), _record(contract="i2607-C-750", match_id="OPT")], size=268)
    fills, issues = parse_match_records(path, account=_account(), source_file=_source(path))
    assert [(fill.asset_type, fill.contract) for fill in fills] == [
        ("future", "i2607"),
        ("option", "i2607-c-750"),
    ]
    assert not issues


def test_full_match_parser_accepts_padded_layout_and_keeps_source_immutable(tmp_path):
    path = tmp_path / "20260903match-v2.dat"
    _write_match(path, [_record(contract="i2607", match_id="FUT-V2", size=269)], size=269)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    fills, issues = parse_match_records(path, account=_account(), source_file=_source(path))
    assert len(fills) == 1
    assert fills[0].asset_type == "future"
    assert fills[0].parser_version == "wh6-match-v2-padded"
    assert not issues
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_two_independent_same_signature_occurrences_are_retained(tmp_path):
    path = tmp_path / "20260903match.dat"
    _write_match(path, [_record(match_id=""), _record(match_id="")], size=268)
    fills, issues = parse_match_records(path, account=_account(), source_file=_source(path))
    assert not issues
    assert len(fills) == 2
    assert fills[0].source_event_key != fills[1].source_event_key
    assert fills[0].source_event_key.endswith(":0")
    assert fills[1].source_event_key.endswith(":1")


def test_position_parser_accepts_json_multi_contract_and_empty_complete_snapshot(tmp_path):
    path = tmp_path / "20260903position.dat"
    write_position_json(path, rows=[_position_row(), _position_row("i2607-P-740", direction="short", quantity=2)])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot, issues = parse_position_snapshot(path, account=_account(), source_file=_position_source(path))
    assert snapshot is not None
    assert snapshot.complete is True
    assert [row.asset_type for row in snapshot.rows] == ["option", "option"]
    assert [row.contract for row in snapshot.rows] == ["i2607-c-750", "i2607-p-740"]
    assert snapshot.rows[0].quantity == 3
    assert snapshot.rows[0].option_kind == "c"
    assert snapshot.to_payload()["rows"][0]["contract"] == "i2607-c-750"
    assert not issues
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    write_position_json(path, rows=[])
    empty, empty_issues = parse_position_snapshot(path, account=_account(), source_file=_position_source(path))
    assert empty is not None and empty.complete and empty.rows == ()
    assert not empty_issues


def test_position_parser_accepts_only_registered_binary_layout(tmp_path):
    path = tmp_path / "20260903position.bin"
    write_position_binary(path, rows=[_position_row("i2607")])
    source = _position_source(path)
    snapshot, issues = parse_position_snapshot(path, account=_account(), source_file=source)
    assert snapshot is not None
    assert snapshot.rows[0].asset_type == "future"
    assert snapshot.rows[0].contract == "i2607"
    assert snapshot.parser_version == "wh6-position-binary-v1"
    assert not issues


def test_position_parser_rejects_unknown_truncated_and_incomplete_snapshots(tmp_path):
    unknown = tmp_path / "unknown-position.dat"
    unknown.write_bytes(b"not a position cache")
    with pytest.raises(ValueError):
        parse_position_snapshot(unknown, account=_account(), source_file=_position_source(unknown))

    truncated = tmp_path / "20260903position.bin"
    write_position_binary(truncated, rows=[_position_row()])
    truncated.write_bytes(truncated.read_bytes()[:-7])
    snapshot, issues = parse_position_snapshot(truncated, account=_account(), source_file=_position_source(truncated))
    assert snapshot is None
    assert any(issue.code == "truncated_position_snapshot" for issue in issues)

    incomplete = tmp_path / "20260903position.dat"
    write_position_json(incomplete, rows=[], complete=False)
    snapshot, issues = parse_position_snapshot(incomplete, account=_account(), source_file=_position_source(incomplete))
    assert snapshot is None
    assert any(issue.code == "incomplete_position_snapshot" for issue in issues)


def test_position_parser_quarantines_unknown_contract_and_malformed_row(tmp_path):
    path = tmp_path / "20260903position.dat"
    write_position_json(path, rows=[_position_row("stock-600000")])
    snapshot, issues = parse_position_snapshot(path, account=_account(), source_file=_position_source(path))
    assert snapshot is None
    assert any(issue.code == "invalid_position_row" for issue in issues)


def test_discovery_and_manual_validation_include_only_explicit_position_cache(tmp_path):
    root = tmp_path / "WH6" / "Users" / "u1" / "Record"
    root.mkdir(parents=True)
    position = root / "20260903position.dat"
    write_position_json(position, rows=[])
    (root / "20260903order.dat").write_bytes(b"order")

    candidates = discover_wh6_sources([tmp_path / "WH6"])
    assert [item.path for item in candidates] == [position]
    assert candidates[0].kind == "position"
    assert "position" in candidates[0].validation_reason
    assert validate_sources(root)[0].kind == "position"
