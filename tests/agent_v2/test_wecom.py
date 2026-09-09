import pytest
from app import db
from app.trading_agent import wecom, store
from test_store import queued


def test_group_and_attachment_frames_do_not_enter_queue():
    assert wecom.parse_text_frame({"body":{"msgid":"x","from":{"userid":"u"},"chattype":"group","text":{"content":"查持仓"}}}) is None
    assert wecom.parse_text_frame({"body":{"msgid":"x","from":{"userid":"u"},"chattype":"single","image":{"url":"private"}}}) is None


def test_duplicate_message_id_maps_to_same_uuid():
    frame={"body":{"msgid":"same","from":{"userid":"u"},"chattype":"single","text":{"content":"查持仓"}}}
    assert wecom.parse_text_frame(frame)["request_id"]==wecom.parse_text_frame(frame)["request_id"]


class FakeClient:
    def __init__(self):
        self.replies = []

    async def reply_stream(self, frame, stream_id, content, finish=False):
        self.replies.append((stream_id, content, finish))


@pytest.mark.asyncio
async def test_duplicate_wecom_message_enqueues_once_and_final_delivery_is_single_attempt(queued, monkeypatch):
    uid, _, _ = queued
    monkeypatch.setenv("WECOM_BOT_ID", "synthetic-bot")
    with db.connect() as conn:
        conn.execute("""INSERT INTO agent_v2_wecom_bindings(bot_id,wecom_user_id,user_id,status,created_at)
            VALUES ('synthetic-bot','external-user',?,'active',?)""", (uid, store.stamp()))
    frame = {"headers": {"req_id": "req-1"}, "body": {"msgid": "same-id", "from": {"userid": "external-user"},
        "chattype": "single", "stream_id": "stream-1", "text": {"content": "查全部持仓"}}}
    client = FakeClient()
    first = await wecom.handle_text(frame, client, bot_id="synthetic-bot")
    second = await wecom.handle_text(frame, client, bot_id="synthetic-bot")
    assert first == second
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM closing_review_tasks WHERE task_kind='v2_user_message'").fetchone()[0]
    assert count == 2  # one fixture task plus one idempotently enqueued WeCom task
    claimed = store.claim_next("wecom-worker")
    if claimed != first:
        store.finish(claimed, "wecom-worker", "succeeded", "fixture complete")
        claimed = store.claim_next("wecom-worker")
    assert claimed == first
    store.finish(first, "wecom-worker", "succeeded", "已完成", structured_payload={"schema_version": "2.0", "status": "complete"})
    assert await wecom.deliver_task(first)
    assert client.replies[-1] == ("stream-1", "已完成", True)
    with db.connect() as conn:
        assert conn.execute("SELECT delivery_state FROM agent_v2_runs WHERE task_id=?", (first,)).fetchone()[0] == "delivered"


@pytest.mark.asyncio
async def test_pair_code_binds_without_creating_business_task(queued, monkeypatch):
    uid, _, _ = queued
    monkeypatch.setenv("WECOM_BOT_ID", "synthetic-bot")
    code = store.issue_pair_code(uid)
    frame = {"body": {"msgid": "pair-1", "from": {"userid": "external-user"}, "chattype": "single",
        "text": {"content": code}}}
    client = FakeClient()
    assert await wecom.handle_text(frame, client, bot_id="synthetic-bot") == "paired"
    with db.connect() as conn:
        binding = conn.execute("SELECT user_id FROM agent_v2_wecom_bindings WHERE bot_id='synthetic-bot' AND wecom_user_id='external-user'").fetchone()
        task_count = conn.execute("SELECT COUNT(*) FROM closing_review_tasks WHERE task_kind='v2_user_message'").fetchone()[0]
    assert binding[0] == uid and task_count == 1  # the fixture task is unrelated to pairing


def test_build_client_uses_official_sdk_without_connecting(monkeypatch):
    monkeypatch.setenv("WECOM_BOT_ID", "synthetic-bot")
    monkeypatch.setenv("WECOM_BOT_SECRET", "synthetic-secret")
    client = wecom.build_client()
    assert type(client).__name__ == "WSClient"
    assert client.listeners("message.text")


def test_bot_lease_prevents_two_workers_from_sharing_one_connection(tmp_path):
    first = wecom.BotLease("synthetic-bot", path=str(tmp_path / "bot.lock")).acquire()
    second = wecom.BotLease("synthetic-bot", path=str(tmp_path / "bot.lock"))
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
