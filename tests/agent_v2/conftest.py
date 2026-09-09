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


@pytest.fixture
def pilot(monkeypatch):
    from app import db

    db.init_db()
    monkeypatch.setenv("AGENT_V2_PILOT_USERNAME", "synthetic_pilot")
    with db.connect() as conn:
        uid = conn.execute(
            "INSERT INTO users(name,username,department,password_hash,role) VALUES ('Pilot','synthetic_pilot','期货组','not-a-password','用户')"
        ).lastrowid
        for module in ("closing_review_agent", "trading_positions"):
            conn.execute(
                "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?, ?, 1, 0)",
                (uid, module),
            )
        cid = conn.execute(
            "INSERT INTO closing_review_conversations(user_id,channel,kind,title) VALUES (?,'web','v2_conversation','test')",
            (uid,),
        ).lastrowid
    return uid, cid
