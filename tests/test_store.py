import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
from hermes_a2a_bridge.store import Store
from hermes_a2a_bridge.files import public_file_metadata
from hermes_a2a_bridge.operations import attach_file_artifact, ingest_local_file


def test_tasks_and_registry_round_trip(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    message = Message(role="user", parts=[{"text": "hello"}])
    task = Task(id="t1", contextId="c1", status=TaskStatus(state=TaskState.SUBMITTED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    assert store.get_task("t1").status.state == TaskState.SUBMITTED
    store.update_task("t1", TaskState.COMPLETED, {"message": Message(role="agent", parts=[{"text": "hi"}]).model_dump(by_alias=True, mode="json")})
    assert store.list_tasks()[0].status.state == TaskState.COMPLETED
    store.registry_add("demo", "http://agent", "secret")
    assert store.registry_get("demo")["token"] == "secret"
    assert store.registry_list()[0]["hasToken"] is True
    assert "token" not in store.registry_list()[0]
    assert store.registry_remove("demo") is True


def test_update_missing_task_and_registry_upsert_behavior(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    assert store.update_task("missing", TaskState.COMPLETED) is False
    store.registry_add("demo", "http://agent", "secret")
    store.registry_add("demo", "http://agent-two")
    row = store.registry_get("demo")
    assert row["url"] == "http://agent-two"
    assert row["token"] == "secret"
    assert store.registry_remove("missing") is False


def test_task_events_table_and_ordered_resume_round_trip(tmp_path):
    path = tmp_path / "events.sqlite3"
    store = Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_events'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_attachments'"
        ).fetchone()
    first = store.add_task_event("t1", {"task": {"id": "t1"}})
    second = store.add_task_event("t1", {"statusUpdate": {"taskId": "t1"}})
    assert second > first
    assert [event.id for event in store.list_task_events("t1")] == [first, second]
    newer = store.list_task_events("t1", after_event_id=first)
    assert [event.id for event in newer] == [second]
    assert newer[0].event == {"statusUpdate": {"taskId": "t1"}}
    assert store.get_task_event("t1", second) == newer[0]


def test_file_attachment_metadata_crud_and_public_shape(tmp_path):
    path = tmp_path / "files.sqlite3"
    store = Store(path)
    item = store.add_file_attachment(
        file_id="file_abcdefghijklmnopqrstuv",
        task_id="task-1",
        artifact_id="artifact-1",
        filename="../report.pdf",
        safe_filename="report.pdf",
        mime_type="application/pdf",
        declared_mime_type="application/pdf",
        size_bytes=12345,
        sha256="a" * 64,
        storage_path=str(tmp_path / "storage" / "content"),
        source="local",
        source_url="https://user:pass@example.test/file.pdf?token=secret",
        metadata={"purpose": "test"},
    )
    assert item["metadata"] == {"purpose": "test"}
    assert store.get_file_attachment(item["id"])["sha256"] == "a" * 64
    assert [row["id"] for row in store.list_file_attachments(task_id="task-1")] == [item["id"]]
    assert [row["id"] for row in store.list_file_attachments(artifact_id="artifact-1")] == [item["id"]]
    assert store.count_file_attachments() == 1
    assert store.file_storage_stats() == {
        "file_attachment_count": 1,
        "file_attachment_bytes": 12345,
    }
    public = public_file_metadata(item)
    assert public == {
        "fileId": "file_abcdefghijklmnopqrstuv",
        "name": "report.pdf",
        "mimeType": "application/pdf",
        "sizeBytes": 12345,
        "sha256": "a" * 64,
        "source": "local",
        "createdAt": item["created_at"],
        "metadata": {"purpose": "test"},
        "sourceUrl": "https://example.test/file.pdf",
    }
    assert "storage_path" not in public
    assert "storagePath" not in public
    assert "token" not in str(public)

    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(file_attachments)").fetchall()]
        assert "content" not in columns
        raw = conn.execute("SELECT metadata_json FROM file_attachments WHERE id=?", (item["id"],)).fetchone()[0]
    assert raw == '{"purpose":"test"}'
    assert store.delete_file_attachment(item["id"]) is True
    assert store.delete_file_attachment(item["id"]) is False
    assert store.count_file_attachments() == 0


def test_old_0_3_5_file_attachment_schema_migrates_nullable_remote_url_columns(tmp_path):
    path = tmp_path / "old-035.sqlite3"
    message = Message(role="user", parts=[{"text": "old task"}])
    request_json = json.dumps({"message": message.model_dump(by_alias=True, mode="json")})
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                context_id TEXT,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT,
                error TEXT,
                metadata_json TEXT
            );
            CREATE TABLE registry (
                name TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                token TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_json TEXT NOT NULL
            );
            CREATE TABLE file_attachments (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                artifact_id TEXT,
                filename TEXT,
                safe_filename TEXT,
                mime_type TEXT,
                declared_mime_type TEXT,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
        """)
        now = "2026-06-24T00:00:00+00:00"
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-task", "ctx", TaskState.COMPLETED.value, now, now, request_json, None, None, "{}"),
        )
        conn.execute(
            "INSERT INTO registry VALUES (?, ?, ?, ?, ?)",
            ("demo", "http://agent.example", "saved-token", now, now),
        )
        conn.execute(
            "INSERT INTO task_events (task_id, created_at, event_json) VALUES (?, ?, ?)",
            ("old-task", now, '{"statusUpdate":{"taskId":"old-task"}}'),
        )
        conn.execute(
            """INSERT INTO file_attachments (
                   id, task_id, artifact_id, filename, safe_filename, mime_type,
                   declared_mime_type, size_bytes, sha256, storage_path, created_at,
                   source, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "file_abcdefghijklmnopqrstuv", "old-task", "artifact-old", "old.txt",
                "old.txt", "text/plain", "text/plain", 5, "b" * 64,
                "controlled/storage/content", now, "local", '{"legacy":true}',
            ),
        )

    store = Store(path)

    old = store.get_file_attachment("file_abcdefghijklmnopqrstuv")
    assert old["storage_path"] == "controlled/storage/content"
    assert old["size_bytes"] == 5
    assert old["sha256"] == "b" * 64
    assert old["metadata"] == {"legacy": True}
    assert store.get_task("old-task") is not None
    assert store.registry_get("demo")["token"] == "saved-token"
    assert store.count_task_events("old-task") == 1

    remote = store.add_file_attachment(
        file_id="file_remoteabcdefghijklmnop",
        task_id="old-task",
        artifact_id="artifact-remote",
        filename="remote.pdf",
        safe_filename="remote.pdf",
        mime_type="application/pdf",
        declared_mime_type="application/pdf",
        size_bytes=None,
        sha256=None,
        storage_path=None,
        source="remote_url",
        source_url="https://example.test/remote.pdf",
        metadata={"remote": True},
    )
    public = public_file_metadata(remote)
    assert public["metadataOnly"] is True
    assert public["bytesAvailable"] is False
    assert store.file_storage_stats() == {
        "file_attachment_count": 2,
        "file_attachment_bytes": 5,
    }
    with sqlite3.connect(path) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(file_attachments)").fetchall()}
        assert columns["size_bytes"][3] == 0
        assert columns["sha256"][3] == 0
        assert columns["storage_path"][3] == 0
        assert "source_url" in columns


def test_attach_file_artifact_appends_task_and_persists_safe_event(tmp_path, config):
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    config["server"]["public_url"] = "http://127.0.0.1:8765"
    store = Store(tmp_path / "attach.sqlite3")
    message = Message(role="user", parts=[{"text": "attach"}])
    store.insert_task(
        Task(id="task-1", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    file_id = ingest_local_file(source, store, config, metadata={"kind": "report"})["file"]["fileId"]
    before = source.read_bytes()

    result = attach_file_artifact(
        store,
        config,
        file_id,
        "task-1",
        artifact_id="artifact-file",
        name="Report",
    )

    task = store.get_task("task-1")
    artifact = task.artifacts[0]
    part = artifact["parts"][0]
    assert result["success"] is True
    assert artifact["artifactId"] == "artifact-file"
    assert part["file"]["fileId"] == file_id
    assert part["file"]["uri"] == f"http://127.0.0.1:8765/files/{file_id}"
    assert part["metadata"] == {"kind": "report"}
    stored_event = store.list_task_events("task-1")[-1].event
    event_part = stored_event["artifactUpdate"]["artifact"]["parts"][0]
    assert event_part["file"]["fileId"] == file_id
    serialized = json.dumps(stored_event)
    assert "hello" not in serialized
    assert "storage_path" not in serialized
    assert str(tmp_path) not in serialized
    row = store.get_file_attachment(file_id)
    assert Path(row["storage_path"]).read_bytes() == before


def test_attach_file_artifact_rejects_unknown_file_and_task(tmp_path, config):
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    store = Store(tmp_path / "attach-errors.sqlite3")
    message = Message(role="user", parts=[{"text": "attach"}])
    store.insert_task(
        Task(id="task-1", status=TaskStatus(state=TaskState.COMPLETED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    try:
        attach_file_artifact(store, config, "file_missingabcdefghijklmn", "task-1")
    except Exception as exc:
        assert getattr(exc, "code") == "file_not_found"
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    file_id = ingest_local_file(source, store, config)["file"]["fileId"]
    try:
        attach_file_artifact(store, config, file_id, "missing-task")
    except Exception as exc:
        assert getattr(exc, "code") == "task_not_found"


def test_event_counts_bounds_and_retention_are_deterministic(tmp_path):
    path = tmp_path / "retention.sqlite3"
    store = Store(path)
    ids = {task_id: [store.add_task_event(task_id, {"n": n}) for n in range(4)] for task_id in ("a", "b")}
    assert store.count_task_events() == 8
    assert store.count_task_events("a") == 4
    bounds = store.get_event_bounds()
    assert bounds["oldest_event_id"] == ids["a"][0]
    assert bounds["newest_event_id"] == ids["b"][-1]

    result = store.prune_task_events(max_events_per_task=2)
    assert result == {"deleted_count": 4, "remaining_count": 4, "affected_task_count": 2}
    assert [event.id for event in store.list_task_events("a")] == ids["a"][-2:]
    assert [event.id for event in store.list_task_events("b")] == ids["b"][-2:]


def test_age_pruning_never_deletes_tasks_or_registry(tmp_path):
    path = tmp_path / "age.sqlite3"
    store = Store(path)
    message = Message(role="user", parts=[{"text": "keep task"}])
    store.insert_task(Task(id="keep", status=TaskStatus(state=TaskState.COMPLETED)), {"message": message.model_dump(by_alias=True, mode="json")})
    store.registry_add("keep", "http://agent", "secret")
    old_id = store.add_task_event("keep", {"old": True})
    new_id = store.add_task_event("keep", {"new": True})
    old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE task_events SET created_at=? WHERE id=?", (old_time, old_id))
    result = store.prune_task_events(older_than_days=30)
    assert result["deleted_count"] == 1
    assert [event.id for event in store.list_task_events("keep")] == [new_id]
    assert store.get_task("keep") is not None
    assert store.registry_get("keep")["token"] == "secret"


def test_stale_recovery_marks_only_old_tasks_and_persists_status(tmp_path):
    path = tmp_path / "recovery.sqlite3"
    store = Store(path)
    for task_id in ("stale", "fresh"):
        message = Message(role="user", parts=[{"text": task_id}])
        store.insert_task(
            Task(id=task_id, contextId="ctx", status=TaskStatus(state=TaskState.WORKING), history=[message]),
            {"message": message.model_dump(by_alias=True, mode="json")},
        )
        store.update_task(task_id, TaskState.WORKING)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE tasks SET updated_at=? WHERE id='stale'", (old_time,))

    result = store.recover_stale_tasks(
        {TaskState.SUBMITTED, TaskState.WORKING, TaskState.INPUT_REQUIRED}, 900,
    )
    assert result == {"recovered_count": 1, "skipped_count": 1, "recovered_task_ids": ["stale"]}
    assert store.get_task("stale").status.state == TaskState.FAILED
    assert store.get_task("fresh").status.state == TaskState.WORKING
    event = store.list_task_events("stale")[-1].event["statusUpdate"]
    assert event["status"]["state"] == "TASK_STATE_FAILED"
    assert "previous server process exited" in event["status"]["message"]["parts"][0]["text"]


def _insert_working(store, task_id):
    message = Message(role="user", parts=[{"text": task_id}])
    store.insert_task(
        Task(id=task_id, contextId="ctx", status=TaskStatus(state=TaskState.WORKING), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )


def test_task_lease_ownership_heartbeat_takeover_and_release(tmp_path):
    path = tmp_path / "leases.sqlite3"
    store = Store(path)
    _insert_working(store, "owned")
    assert store.acquire_task_lease("owned", "one", 101, 60)
    original = store.get_task_lease("owned")
    assert not store.acquire_task_lease("owned", "two", 202, 60)
    assert store.heartbeat_task_lease("owned", "one", 120)
    assert not store.heartbeat_task_lease("owned", "two", 120)
    assert store.get_task_lease("owned")["lease_expires_at"] >= original["lease_expires_at"]
    assert not store.release_task_lease("owned", "two")

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE task_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id='owned'"
        )
    assert [lease["task_id"] for lease in store.list_expired_leases()] == ["owned"]
    assert store.acquire_task_lease("owned", "two", 202, 60)
    assert store.get_task_lease("owned")["owner_instance_id"] == "two"
    assert store.release_task_lease("owned", "two")
    assert store.get_task_lease("owned") is None


def test_expired_lease_recovery_persists_status_and_skips_live_lease(tmp_path):
    path = tmp_path / "lease-recovery.sqlite3"
    store = Store(path)
    for task_id in ("expired", "live"):
        _insert_working(store, task_id)
        assert store.acquire_task_lease(task_id, task_id, 1, 60)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE task_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id='expired'"
        )
    result = store.recover_expired_leases()
    assert result["recovered_task_ids"] == ["expired"]
    assert store.get_task("expired").status.state == TaskState.FAILED
    assert store.get_task("live").status.state == TaskState.WORKING
    event = store.list_task_events("expired")[-1].event["statusUpdate"]
    assert event["status"]["state"] == TaskState.FAILED.value
    assert event["status"]["message"]["parts"][0]["text"] == (
        "Task marked failed because its executor lease expired before completion."
    )


def test_sqlite_stats_include_contention_and_ownership_observability(tmp_path):
    store = Store(
        tmp_path / "stats.sqlite3",
        {"busy_timeout_ms": 2345, "journal_mode": "WAL", "synchronous": "NORMAL"},
    )
    stats = store.maintenance_stats()
    assert stats["busy_timeout_ms"] == 2345
    assert stats["journal_mode"].lower() in {"wal", "delete"}
    assert stats["database_size_bytes"] > 0
    assert stats["database_path"].endswith("stats.sqlite3")
    assert stats["lease_count"] == 0
    assert stats["expired_lease_count"] == 0
    assert stats["file_attachment_count"] == 0
    assert stats["file_attachment_bytes"] == 0


def test_unsupported_sqlite_pragma_is_warning_not_startup_failure(tmp_path):
    store = Store(
        tmp_path / "pragma-warning.sqlite3",
        {"busy_timeout_ms": "invalid", "journal_mode": "not-a-mode", "synchronous": "wild"},
    )
    message = Message(role="user", parts=[{"text": "still works"}])
    store.insert_task(
        Task(id="ok", status=TaskStatus(state=TaskState.SUBMITTED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    assert store.get_task("ok") is not None
    warnings = store.maintenance_stats()["sqlite_warnings"]
    assert any("busy_timeout" in warning for warning in warnings)
    assert any("journal_mode" in warning for warning in warnings)
    assert any("synchronous" in warning for warning in warnings)
