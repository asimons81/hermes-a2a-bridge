import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_a2a_bridge.errors import DatabaseBusyError
from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
from hermes_a2a_bridge.server import EXECUTOR_MANAGER_KEY, INSTANCE_ID_KEY, create_app
from hermes_a2a_bridge.store import Store


AUTH = {"Authorization": "Bearer test-secret-token"}


def _insert_task(store, task_id, state=TaskState.WORKING):
    message = Message(role="user", parts=[{"text": task_id}])
    store.insert_task(
        Task(id=task_id, contextId="ctx", status=TaskStatus(state=state), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )


def test_cancellation_request_lifecycle_and_ttl_expiry(tmp_path):
    store = Store(tmp_path / "cancellations.sqlite3")
    request = store.create_cancellation_request("t1", "requester", "owner", 60, "stop")
    assert request["status"] == "pending"
    assert store.get_pending_cancellation_for_owner("t1", "owner")["id"] == request["id"]
    assert not store.acknowledge_cancellation_request(request["id"], "wrong-owner")
    assert store.acknowledge_cancellation_request(request["id"], "owner")
    assert store.complete_cancellation_request(request["id"], "owner")
    completed = store.list_cancellation_requests(task_id="t1")[0]
    assert completed["status"] == "completed"
    assert completed["acknowledged_at"] and completed["completed_at"]

    expiring = store.create_cancellation_request("t2", "requester", "owner", 1)
    future = datetime.now(timezone.utc) + timedelta(seconds=2)
    result = store.expire_cancellation_requests(future)
    assert result == {"expired_count": 1, "expired_request_ids": [expiring["id"]]}
    assert store.list_cancellation_requests(task_id="t2")[0]["status"] == "expired"
    store.create_cancellation_request("t3", "requester", "owner", 60)
    stats = store.maintenance_stats()
    assert stats["pending_cancellation_count"] == 1
    assert stats["expired_cancellation_count"] == 1


def test_lease_diagnostics_and_stats_warning_flags(tmp_path):
    path = tmp_path / "diagnostics.sqlite3"
    store = Store(path)
    now = datetime.now(timezone.utc)
    for task_id in ("soon", "expired"):
        _insert_task(store, task_id)
        assert store.acquire_task_lease(task_id, f"owner-{task_id}", 7, 60)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """UPDATE task_leases SET acquired_at=?, heartbeat_at=?, lease_expires_at=?
               WHERE task_id='soon'""",
            ((now - timedelta(seconds=30)).isoformat(), (now - timedelta(seconds=25)).isoformat(),
             (now + timedelta(seconds=5)).isoformat()),
        )
        conn.execute(
            """UPDATE task_leases SET acquired_at=?, heartbeat_at=?, lease_expires_at=?
               WHERE task_id='expired'""",
            ((now - timedelta(seconds=60)).isoformat(), (now - timedelta(seconds=30)).isoformat(),
             (now - timedelta(seconds=1)).isoformat()),
        )
    diagnostics = {item["task_id"]: item for item in store.lease_diagnostics(20, now)}
    assert diagnostics["soon"]["state"] == TaskState.WORKING.value
    assert diagnostics["soon"]["lease_age_seconds"] == 30
    assert diagnostics["soon"]["heartbeat_age_seconds"] == 25
    assert diagnostics["soon"]["seconds_until_expiry"] == 5
    assert diagnostics["soon"]["lease_expiring_soon"] is True
    assert diagnostics["soon"]["heartbeat_stale"] is True
    assert diagnostics["soon"]["expired"] is False
    assert diagnostics["expired"]["expired"] is True

    stats = store.maintenance_stats(20)
    assert stats["active_lease_count"] == 1
    assert stats["expired_lease_count"] == 1
    assert stats["stale_heartbeat_count"] == 2
    assert "sqlite_warning_count" in stats


def test_sqlite_retry_is_bounded_and_only_retries_transient_errors(tmp_path):
    store = Store(
        tmp_path / "retry.sqlite3", fault_config={
            "sqlite_retry_attempts": 2, "sqlite_retry_backoff_seconds": 0,
        },
    )
    calls = []

    def eventually_succeeds():
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert store.run_with_sqlite_retry(eventually_succeeds, label="test") == "ok"
    assert len(calls) == 3

    with pytest.raises(sqlite3.OperationalError, match="syntax error"):
        store.run_with_sqlite_retry(
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("syntax error")),
            label="permanent",
        )

    def always_locked():
        raise sqlite3.OperationalError("database is locked near test-secret-token")

    with pytest.raises(DatabaseBusyError) as caught:
        store.run_with_sqlite_retry(always_locked, label="controlled")
    assert "after 3 attempts" in str(caught.value)
    assert "test-secret-token" not in str(caught.value)


def test_real_sqlite_lock_exhaustion_is_controlled_and_recovers(tmp_path):
    path = tmp_path / "locked.sqlite3"
    store = Store(
        path,
        {"busy_timeout_ms": 1},
        {"sqlite_retry_attempts": 1, "sqlite_retry_backoff_seconds": 0},
    )
    blocker = sqlite3.connect(path, timeout=0.001)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(DatabaseBusyError, match="database remained busy or locked"):
            store.add_task_event("t1", {"safe": True})
    finally:
        blocker.rollback()
        blocker.close()
    assert store.add_task_event("t1", {"safe": True}) > 0


async def test_non_owner_request_is_honored_by_owner_heartbeat(config, tmp_path):
    config["executor"]["command"] = [
        sys.executable, "-c", "import time; time.sleep(30)", "{prompt}",
    ]
    config["executor"]["cancel_grace_seconds"] = 0.05
    config["ownership"]["heartbeat_interval_seconds"] = 0.02
    config["cancellation"]["poll_interval_seconds"] = 0.01
    path = tmp_path / "cooperative.sqlite3"
    owner_store = Store(path)
    owner = TestClient(TestServer(create_app(config, owner_store)))
    await owner.start_server()
    requester = None
    try:
        stream = await owner.post(
            "/message:stream", headers=AUTH,
            json={"message": {"role": "user", "parts": [{"text": "cancel cooperatively"}]}},
        )
        first = (await stream.content.readuntil(b"\n\n")).decode()
        task_id = json.loads(first.split("data: ", 1)[1])["task"]["id"]
        await stream.content.readuntil(b"\n\n")
        for _ in range(50):
            if await owner.app[EXECUTOR_MANAGER_KEY].has_process(task_id):
                break
            await asyncio.sleep(0.005)
        assert await owner.app[EXECUTOR_MANAGER_KEY].has_process(task_id)

        requester = TestClient(TestServer(create_app(config, Store(path))))
        await requester.start_server()
        response = await requester.post(f"/tasks/{task_id}:cancel", headers=AUTH)
        payload = await response.json()
        assert response.status == 409
        assert payload["code"] == "cancellation_requested"
        assert payload["owner_instance_id"] == owner.app[INSTANCE_ID_KEY]
        assert "recorded" in payload["error"]
        assert "terminated" not in payload["error"].lower()
        assert "test-secret-token" not in json.dumps(payload)

        await asyncio.wait_for(stream.text(), timeout=2)
        assert owner_store.get_task(task_id).status.state == TaskState.CANCELED
        assert not await owner.app[EXECUTOR_MANAGER_KEY].has_process(task_id)
        request = owner_store.list_cancellation_requests(task_id=task_id)[0]
        assert request["status"] == "completed"
        assert request["acknowledged_at"] and request["completed_at"]
        event = owner_store.list_task_events(task_id)[-1].event["statusUpdate"]
        assert event["status"]["state"] == TaskState.CANCELED.value
    finally:
        if requester is not None:
            await requester.close()
        await owner.close()


def test_expired_owner_recovery_expires_pending_cancellation(tmp_path):
    path = tmp_path / "crash-recovery.sqlite3"
    store = Store(path)
    _insert_task(store, "crashed")
    assert store.acquire_task_lease("crashed", "gone", 999, 60)
    request = store.create_cancellation_request("crashed", "requester", "gone", 300)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE task_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id='crashed'"
        )
    result = store.recover_expired_leases()
    assert result["recovered_task_ids"] == ["crashed"]
    assert store.get_task("crashed").status.state == TaskState.FAILED
    assert store.list_cancellation_requests(task_id="crashed")[0] == {
        **request,
        "status": "expired",
    }
