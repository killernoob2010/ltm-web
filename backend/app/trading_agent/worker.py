"""Standalone worker entrypoint; importing it never imports the Web app."""
import asyncio
import os
from typing import Any

import uvicorn

from . import harness, mcp_client, mcp_server, model, resources, store


async def worker_once(deps: harness.RuntimeDeps):
    task_id = deps.store.claim_next(deps.worker_id)
    if task_id is None:
        return None
    heartbeat_stop = asyncio.Event()

    async def renew():
        while not heartbeat_stop.is_set():
            try:
                await asyncio.wait_for(heartbeat_stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                if not deps.store.heartbeat(task_id, deps.worker_id):
                    return

    renew_task = asyncio.create_task(renew())
    try:
        result = await harness.run_task(task_id, deps)
        # WeCom keeps only an in-memory reply context; a restart never blindly
        # replays a business answer. Web tasks simply have no pending delivery.
        from . import wecom

        await wecom.deliver_task(task_id)
        return result
    finally:
        heartbeat_stop.set()
        renew_task.cancel()
        await asyncio.gather(renew_task, return_exceptions=True)


async def worker_loop(deps: harness.RuntimeDeps, *, stop_event: asyncio.Event | None = None,
                      idle_seconds=1, resource_guard=None):
    stop_event = stop_event or asyncio.Event()
    deps.store.recover_interrupted()
    while not stop_event.is_set():
        if resource_guard is not None:
            decision = resource_guard.admit()
            if not decision.allowed:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=idle_seconds)
                except asyncio.TimeoutError:
                    pass
                continue
        result = await worker_once(deps)
        if result is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=idle_seconds)
            except asyncio.TimeoutError:
                pass


async def worker_main():
    if os.environ.get("AGENT_V2_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("AGENT_V2_ENABLED 未开启")
    # Migrations are an explicit deployment step; fail clearly if absent.
    with store.db.connect() as conn:
        required = store.db._exec(conn.cursor(), "SELECT name FROM sqlite_master WHERE name='agent_v2_runs'").fetchone() if not store.db._is_pg() else store.db._exec(conn.cursor(), "SELECT to_regclass('public.agent_v2_runs') AS name").fetchone()
    if not required or not required["name"]:
        raise RuntimeError("Agent V2 附表尚未迁移")
    store.mark_orphaned_deliveries()
    app = mcp_server.build_mcp_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.environ.get("AGENT_V2_MCP_PORT", "8766")), log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    wecom_client = None
    bot_lease = None
    try:
        while not server.started:
            await asyncio.sleep(.01)
        if os.environ.get("AGENT_V2_WECOM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            from . import wecom

            bot_lease = wecom.BotLease(os.environ.get("WECOM_BOT_ID", "")).acquire()
            wecom_client = wecom.build_client()
            await wecom_client.connect()
        async with mcp_client.MCPToolClient() as mcp:
            deps = harness.RuntimeDeps(store=store, model=model.DeepSeekModel(), mcp=mcp, worker_id=os.environ.get("AGENT_V2_WORKER_ID", "agent-v2"))
            await worker_loop(deps, resource_guard=resources.default_resource_guard())
    finally:
        if wecom_client is not None:
            wecom_client.disconnect()
        if bot_lease is not None:
            bot_lease.release()
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    asyncio.run(worker_main())
