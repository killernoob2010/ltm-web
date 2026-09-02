"""WH6 option-fill collector core contract tests."""

from datetime import datetime
from pathlib import Path
import struct
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))

from wh6_collector.account import account_fingerprint, compare_binding, confirm_weak_binding, probe_source_account
from wh6_collector.formats import MATCH_V1, MATCH_V2_PADDED, detect_layout
from wh6_collector.models import AccountIdentity, SourceFile
from wh6_collector.parser import (
    business_trading_day,
    is_option_contract,
    normalize_contract,
    parse_match_records,
    reference_sessions,
)


def _shifted(value: str, width: int) -> bytes:
    encoded = value.encode("gb18030")
    assert len(encoded) <= width
    raw = bytearray(width)
    raw[: len(encoded)] = bytes(min(255, byte * 2) for byte in encoded)
    return bytes(raw)


def _record(
    *,
    contract="i2607-C-750",
    timestamp="2026-09-02 21:05:03",
    quantity=2,
    price="12.5",
    match_id="M-001",
    side="buy",
    open_close="0",
    size=268,
):
    record = bytearray(size)
    record[0:32] = _shifted(timestamp, 32)
    record[32:64] = _shifted(contract, 32)
    struct.pack_into("<I", record, 120, quantity)
    record[124:140] = _shifted(price, 16)
    record[140:172] = _shifted("ORDER-001", 32)
    record[172:176] = _shifted(side, 4)
    record[176:180] = _shifted(open_close, 4)
    record[180:204] = _shifted("DCE", 24)
    record[204:220] = b"0.80\0" + b"\0" * 11
    record[220:236] = _shifted("0", 16)
    record[236:252] = _shifted(match_id, 16)
    return bytes(record)


def _write_match(path: Path, records: list[bytes], *, size: int, declared_count=None):
    header = bytearray(16)
    struct.pack_into("<I", header, 8, declared_count if declared_count is not None else len(records))
    path.write_bytes(bytes(header) + b"".join(records))


def _source(path: Path) -> SourceFile:
    return SourceFile(
        path=path,
        kind="match",
        label="测试 WH6 成交缓存",
        account_clue="宏源期货 ****1234",
        modified_ns=path.stat().st_mtime_ns,
    )


def _account(*, stable_id="902711111", binding_mode="strong"):
    return AccountIdentity(
        account_code="hongyuan_futures",
        display_name="宏源期货账户",
        masked_label="宏源期货 ****1111",
        stable_id=stable_id,
        fingerprint=account_fingerprint("宏源期货账户", stable_id),
        binding_mode=binding_mode,
        confirmed=True,
    )


def test_option_classifier_accepts_iron_ore_option_and_rejects_future():
    assert is_option_contract("i2607-C-750")
    assert normalize_contract("i2607-C-750") == "i2607-c-750"
    assert is_option_contract("i2607-p-750")
    assert not is_option_contract("i2607")
    assert not is_option_contract("stock-600000")


def test_parser_filters_non_options_and_decodes_both_supported_layouts(tmp_path):
    first = tmp_path / "20260902match.dat"
    second = tmp_path / "20260902match-v2.dat"
    _write_match(
        first,
        [_record(), _record(contract="i2607", match_id="FUT-001")],
        size=MATCH_V1.record_size,
    )
    _write_match(second, [_record(match_id="M-002", size=MATCH_V2_PADDED.record_size)], size=MATCH_V2_PADDED.record_size)

    fills, issues = parse_match_records(first, account=_account(), source_file=_source(first))
    fills_v2, issues_v2 = parse_match_records(second, account=_account(), source_file=_source(second))

    assert len(fills) == 1
    assert not issues
    assert fills[0].asset_type == "option"
    assert fills[0].contract == "i2607-c-750"
    assert fills[0].quantity == 2
    assert fills[0].side == "买"
    assert fills[0].open_close == "开"
    assert fills[0].source_event_key == "tradeid:m-001"
    assert len(fills_v2) == 1
    assert fills_v2[0].parser_version == "wh6-match-v2-padded"
    assert not issues_v2


def test_random_non_option_contracts_never_enter_fill_list(tmp_path):
    contracts = ["i2607", "rb2610", "IF2609", "stock-600000", "p", "i2607-c"]
    for index, contract in enumerate(contracts):
        path = tmp_path / ("20260902match-%s.dat" % index)
        _write_match(path, [_record(contract=contract, match_id="NON-%s" % index)], size=268)
        fills, _ = parse_match_records(path, account=_account(), source_file=_source(path))
        assert fills == []


def test_parser_quarantines_unknown_and_truncated_files_without_writing(tmp_path):
    unknown = tmp_path / "unknown.dat"
    unknown.write_bytes(b"not a supported cache")
    with pytest.raises(ValueError):
        parse_match_records(unknown, account=_account(), source_file=_source(unknown))

    truncated = tmp_path / "20260902match.dat"
    _write_match(truncated, [_record()], size=MATCH_V1.record_size, declared_count=2)
    before = truncated.read_bytes()
    fills, issues = parse_match_records(truncated, account=_account(), source_file=_source(truncated))
    assert len(fills) == 1
    assert any(issue.code == "truncated_file" for issue in issues)
    assert truncated.read_bytes() == before

    tail = tmp_path / "20260902match-tail.dat"
    tail.write_bytes(bytes(bytearray(16)) + _record() + b"partial-tail")
    tail_data = bytearray(tail.read_bytes())
    struct.pack_into("<I", tail_data, 8, 2)
    tail.write_bytes(bytes(tail_data))
    fills, issues = parse_match_records(tail, account=_account(), source_file=_source(tail))
    assert len(fills) == 1
    assert any(issue.code == "truncated_file" for issue in issues)


def test_parser_quarantines_missing_required_direction_fields(tmp_path):
    path = tmp_path / "20260902match.dat"
    record = bytearray(_record())
    record[172:180] = b"\0" * 8
    _write_match(path, [bytes(record)], size=MATCH_V1.record_size)
    fills, issues = parse_match_records(path, account=_account(), source_file=_source(path))
    assert fills == []
    assert any(issue.code == "missing_required_field" for issue in issues)


def test_account_binding_distinguishes_strong_and_weak_identity():
    strong = account_fingerprint("宏源期货账户", "902711111")
    assert strong == account_fingerprint("宏源期货账户", "902711111")
    assert strong != account_fingerprint("宏源期货账户", "902711112")
    assert account_fingerprint("宏源期货账户", None) is None

    weak = confirm_weak_binding("宏源期货 ****1111", "C:/WH6/Record")
    assert weak.binding_mode == "weak"
    assert weak.requires_manual_confirmation is True
    assert weak.confirmed is False


def test_source_account_probe_only_trusts_explicit_broker_metadata(tmp_path):
    path = tmp_path / "Record" / "20260902match.dat"
    path.parent.mkdir()
    path.write_bytes(b"")
    (path.parent / "account.ini").write_text("broker=\u5b8f\u6e90\u671f\u8d27\naccount=902711111\n", encoding="utf-8")
    observed = probe_source_account(path)
    assert observed.fingerprint == account_fingerprint("\u5b8f\u6e90\u671f\u8d27\u8d26\u6237", "902711111")
    assert compare_binding(_account(), observed) == "match"

    (path.parent / "account.ini").write_text("broker=\u5b8f\u6e90\u671f\u8d27\naccount=902711112\n", encoding="utf-8")
    assert compare_binding(_account(), probe_source_account(path)) == "mismatch"
    (path.parent / "account.ini").unlink()
    assert probe_source_account(path).requires_manual_confirmation is True


def test_trading_day_maps_night_session_to_previous_exchange_date():
    assert business_trading_day(datetime(2026, 9, 2, 21, 5, 3)) == "2026-09-02"
    assert business_trading_day(datetime(2026, 9, 3, 1, 5, 3)) == "2026-09-02"
    assert business_trading_day(datetime(2026, 9, 3, 9, 5, 3)) == "2026-09-03"


def test_parser_uses_exchange_trading_day_for_after_midnight_fill(tmp_path):
    path = tmp_path / "20260903match.dat"
    _write_match(path, [_record(timestamp="2026-09-03 01:05:03")], size=268)
    fills, issues = parse_match_records(path, account=_account(), source_file=_source(path))
    assert not issues
    assert fills[0].trade_date == "2026-09-02"


def test_parser_accepts_fractional_trade_time(tmp_path):
    path = tmp_path / "20260902match.dat"
    _write_match(path, [_record(timestamp="01:05:03.125")], size=268)
    fills, issues = parse_match_records(path, account=_account(), source_file=_source(path))
    assert not issues
    assert fills[0].trade_date == "2026-09-01"
    assert fills[0].trade_time == "01:05:03"


def test_reference_sessions_are_business_documented_intervals():
    assert [(item.start.isoformat(), item.end.isoformat()) for item in reference_sessions()] == [
        ("09:00:00", "10:15:00"),
        ("10:15:00", "11:30:00"),
        ("13:30:00", "15:00:00"),
        ("21:00:00", "23:00:00"),
    ]
