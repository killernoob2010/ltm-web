"""Pure checks for the packaged first-run setup helpers."""

import hashlib
from pathlib import Path

from collector.wh6_collector.setup_ui import _record_root, build_device_fingerprint


def test_device_fingerprint_is_stable_and_not_plain_machine_data():
    expected = hashlib.sha256(b"test-machine|1234").hexdigest()
    assert build_device_fingerprint(machine_name="test-machine", mac_value=1234) == expected
    assert "test-machine" not in build_device_fingerprint(machine_name="test-machine", mac_value=1234)


def test_record_root_uses_record_directory_when_present():
    path = Path("C:/WH6/Users/account/Record/20260902match.dat")
    assert _record_root(path) == Path("C:/WH6/Users/account/Record")
