"""Synthetic-only Agent tests; no implicit business database or network access."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).parents[1]))


@pytest.fixture(autouse=True)
def isolated_agent_test(tmp_path, monkeypatch):
    from app import db
    import socket

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent-test.db")

    original_connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
            raise RuntimeError("Agent unit tests prohibit external network access")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
