import asyncio
import json
import sqlite3

from aiohttp.test_utils import TestClient, TestServer

from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
from hermes_a2a_bridge.server import INSTANCE_ID_KEY, _run_task, create_app
from hermes_a2a_bridge.store import Store


AUTH = {"Authorization": "Bearer test-secret-token"}


def _task(task_id, state=TaskState.SUBMITTED):
    message = Message(role="user", parts=[{"text": task_id}])
    task = Task(id=task_id, contextId="ctx", status=TaskStatus(state=state), history=[message])
    return task, {"message": message.model_dump(by_alias=True, mode="json")}


async def _client(config, store):
    client = TestClient(TestServer(create_app(config, store)))
    await client.start_server()
    return client


async def test_execution_acquires_heartbeats_and_releases_lease(config, tmp_path, monkeypatch):
    config["ownership"]["lease_seconds"] = 1
    config["ownership"]["heartbeat_interval_seconds"] = 0.01
    started = asyncio.Event()
    finish = asyncio.Event()

    async def controlled(prompt, config, task_id=None, manager=None):
        started.set()
        await finish.wait()
        return "done"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", controlled)
    store = Store(tmp_path / "heartbeat.sqlite3")
    client = await _client(config, store)
    try:
        response = await client.post(
            "/message:stream", headers=AUTH,
            json={"message": {"role": "user", "parts": [{"text": "wait"}]}},
        )
        initial = (await response.content.readuntil(b"\n\n")).decode()
        task_id = json.loads(initial.split("data: ", 1)[1])["task"]["id"]
        await response.content.readuntil(b"\n\n")
        await asyncio.wait_for(started.wait(), timeout=1)
        first = store.get_task_lease(task_id)
        assert first["owner_instance_id"] == client.app[INSTANCE_ID_KEY]
        await asyncio.sleep(0.04)
        assert store.get_task_lease(task_id)["heartbeat_at"] > first["heartbeat_at"]
        finish.set()
        await asyncio.wait_for(response.text(), timeout=1)
        assert store.get_task_lease(task_id) is None
        assert store.get_task(task_id).status.state == TaskState.COMPLETED
    finally:
        finish.set()
        await client.close()


async def test_execution_failure_and_lease_acquisition_failure_are_clear(config, tmp_path, monkeypatch):
    async def failed(prompt, config, task_id=None, manager=None):
        raise RuntimeError("controlled failure")

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", failed)
    store = Store(tmp_path / "failure.sqlite3")
    client = await _client(config, store)
    try:
        response = await client.post(
            "/message:send", headers=AUTH,
            json={"message": {"role": "user", "parts": [{"text": "fail"}]}},
        )
        payload = await response.json()
        assert payload["status"]["state"] == TaskState.FAILED.value
        assert store.get_task_lease(payload["id"]) is None

        task, request = _task("contended")
        store.insert_task(task, request)
        assert store.acquire_task_lease("contended", "other-instance", 999, 60)
        result = await _run_task(client.app, task, "hello")
        assert result.status.state == TaskState.FAILED
        assert "ownership lease could not be acquired" in result.status.message.parts[0].text
    finally:
        await client.close()


async def test_startup_recovers_expired_lease_but_not_live_lease(config, tmp_path):
    config["recovery"]["stale_task_after_seconds"] = 999999
    path = tmp_path / "startup-leases.sqlite3"
    store = Store(path)
    for task_id in ("expired", "live"):
        task, request = _task(task_id, TaskState.WORKING)
        store.insert_task(task, request)
        store.acquire_task_lease(task_id, task_id, 1, 60)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE task_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id='expired'"
        )
    client = await _client(config, store)
    await client.close()
    assert store.get_task("expired").status.state == TaskState.FAILED
    assert store.get_task("live").status.state == TaskState.WORKING
    assert store.list_task_events("expired")[-1].event["statusUpdate"]["metadata"]["expiredLease"] is True


async def test_replay_gap_rejected_before_sse_and_valid_cursor_replays(config, tmp_path):
    store = Store(tmp_path / "replay-gap.sqlite3")
    task, request = _task("replay", TaskState.COMPLETED)
    store.insert_task(task, request)
    ids = [store.add_task_event("replay", {"message": {"n": n}}) for n in range(3)]
    store.prune_task_events(max_events_per_task=2)
    client = await _client(config, store)
    try:
        gap = await client.post(
            "/tasks/replay:subscribe", headers={**AUTH, "Last-Event-ID": str(ids[0] - 1)},
        )
        payload = await gap.json()
        assert gap.status == 409
        assert payload == {
            "success": False,
            "error": "Requested replay cursor is no longer available because event history was pruned.",
            "code": "replay_gap",
            "task_id": "replay",
            "last_event_id": ids[0] - 1,
            "oldest_available_event_id": ids[1],
        }
        replay = await client.post(
            "/tasks/replay:subscribe", headers={**AUTH, "Last-Event-ID": str(ids[1] - 1)},
        )
        assert replay.status == 200
        assert f"id: {ids[1]}" in await replay.text()
    finally:
        await client.close()


async def test_cancellation_requests_live_other_owner_and_accepts_expired_lease(config, tmp_path):
    config["ownership"]["recover_expired_leases_on_startup"] = False
    path = tmp_path / "cancel-leases.sqlite3"
    store = Store(path)
    for task_id in ("other", "expired"):
        task, request = _task(task_id, TaskState.WORKING)
        store.insert_task(task, request)
        store.acquire_task_lease(task_id, "remote-instance", 123, 60)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE task_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id='expired'"
        )
    client = await _client(config, store)
    try:
        rejected = await client.post("/tasks/other:cancel", headers=AUTH)
        rejected_payload = await rejected.json()
        assert rejected.status == 409
        assert rejected_payload["code"] == "cancellation_requested"
        assert rejected_payload["task_id"] == "other"
        assert rejected_payload["owner_instance_id"] == "remote-instance"
        assert isinstance(rejected_payload["request_id"], int)
        assert "test-secret-token" not in json.dumps(rejected_payload)
        assert store.get_task("other").status.state == TaskState.WORKING
        assert store.list_cancellation_requests(task_id="other")[0]["status"] == "pending"

        canceled = await client.post("/tasks/expired:cancel", headers=AUTH)
        canceled_payload = await canceled.json()
        assert canceled.status == 200
        assert canceled_payload["status"]["state"] == TaskState.CANCELED.value
        assert "owned the lease but no local subprocess handle" in (
            canceled_payload["status"]["message"]["parts"][0]["text"]
        )
        assert store.get_task_lease("expired") is None
        assert store.list_task_events("expired")[-1].event["statusUpdate"]["status"]["state"] == TaskState.CANCELED.value
    finally:
        await client.close()
