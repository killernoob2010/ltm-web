from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import db
from app.trading_agent.auth import resolve_principal, authorize


@pytest.fixture
def pilot(monkeypatch):
    db.init_db()
    monkeypatch.setenv("AGENT_V2_PILOT_USERNAME", "synthetic_pilot")
    with db.connect() as conn:
        uid = conn.execute("INSERT INTO users(name,username,department,password_hash,role) VALUES ('Pilot','synthetic_pilot','期货组','not-a-password','用户')").lastrowid
        for module in ("closing_review_agent", "trading_positions"):
            conn.execute("INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?,?,1,0)", (uid,module))
        cid = conn.execute("INSERT INTO closing_review_conversations(user_id,channel,kind,title) VALUES (?,'web','v2_conversation','test')", (uid,)).lastrowid
    return uid, cid


def test_permission_revocation_takes_effect_with_existing_principal(pilot):
    uid, cid = pilot
    principal = resolve_principal(uid, "web", cid, uuid4())
    authorize(principal, "trading.facts")
    with db.connect() as conn:
        conn.execute("UPDATE module_permissions SET can_view=0 WHERE user_id=? AND module_code='trading_positions'", (uid,))
    with pytest.raises(HTTPException) as error:
        authorize(principal, "trading.facts")
    assert error.value.status_code == 403


def test_disabled_user_cannot_reuse_principal(pilot):
    uid, cid = pilot
    principal = resolve_principal(uid, "web", cid, uuid4())
    with db.connect() as conn:
        conn.execute("UPDATE users SET status='停用' WHERE id=?", (uid,))
    with pytest.raises(HTTPException):
        authorize(principal, "closing_review.agent")


def test_wrong_channel_and_unknown_conversation_are_not_accessible(pilot):
    uid, cid = pilot
    for channel, conversation in (("wecom", cid), ("web", cid + 1000)):
        with pytest.raises(HTTPException) as error:
            resolve_principal(uid, channel, conversation, uuid4())
        assert error.value.status_code == 404
