from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest

from hermes_a2a_bridge import cli
from hermes_a2a_bridge.cli import a2a_command
from hermes_a2a_bridge.errors import ClientError


def test_token_rotate_hides_secret_by_default(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    args = argparse.Namespace(a2a_command="token", token_command="rotate", show_token=False, json=False)
    assert a2a_command(args) == 0
    out = capsys.readouterr().out
    assert "Old tokens stop working immediately" in out
    assert "test-secret-token" not in out


def test_token_rotate_json_shows_secret_only_when_requested(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    args = argparse.Namespace(a2a_command="token", token_command="rotate", show_token=True, json=True)
    assert a2a_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["token"]


def test_registry_add_rejects_invalid_name_as_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    args = argparse.Namespace(
        a2a_command="registry",
        registry_command="add",
        name="bad name",
        url="http://demo.test",
        token=None,
        json=True,
    )
    assert a2a_command(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert payload["code"] == "cli_error"


def test_registry_list_json_omits_tokens(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config
    from hermes_a2a_bridge.store import Store

    save_config(config)
    store = Store(tmp_path / "a2a" / "tasks.sqlite3")
    store.registry_add("demo", "http://demo.test", "secret")
    args = argparse.Namespace(a2a_command="registry", registry_command="list", json=True)
    assert a2a_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agents"][0]["hasToken"] is True
    assert "token" not in payload["agents"][0]


def test_stream_json_prints_one_json_object_per_line(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_card(url):
        return {"name": "Remote", "url": "http://remote.test"}

    async def fake_stream(base, message, token=None, context_id=None, file_ids=None):
        yield {"id": 1, "event": "message", "data": {"task": {"id": "t1", "status": {"state": "TASK_STATE_SUBMITTED"}}}}
        yield {"id": 2, "event": "message", "data": {"statusUpdate": {"taskId": "t1", "status": {"state": "TASK_STATE_COMPLETED"}}}}

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(cli.client, "stream_message", fake_stream)
    args = argparse.Namespace(
        a2a_command="stream",
        agent="http://remote.test",
        message="hello",
        file_id=[],
        token=None,
        json=True,
    )
    assert a2a_command(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["id"] for line in lines] == [1, 2]
    assert all(set(json.loads(line)) == {"id", "event", "data"} for line in lines)


def test_cli_human_output_summarizes_data_artifacts(capsys):
    cli._render_text({
        "task": {
            "id": "t1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"kind": "data", "data": {"a": 1, "b": 2, "c": 3}}]}],
        }
    })
    assert "[data part: object, 3 keys]" in capsys.readouterr().out


def test_cli_human_output_summarizes_file_artifacts_safely(capsys):
    cli._render_text({
        "task": {
            "id": "t1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{
                "parts": [{
                    "file": {
                        "fileId": "file_abcdefghijklmnopqrstuv",
                        "name": "report.pdf",
                        "mimeType": "application/pdf",
                        "sizeBytes": 12345,
                        "uri": "http://127.0.0.1:8765/files/file_abcdefghijklmnopqrstuv",
                    }
                }]
            }],
        }
    })
    output = capsys.readouterr().out
    assert "[file: report.pdf, application/pdf, 12345 bytes, id file_abcdefghijklmnopqrstuv]" in output
    assert "/files/file_" not in output


def test_cli_json_preserves_data_parts(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_card(url):
        return {"name": "Remote", "url": "http://remote.test"}

    async def fake_send(base, message, token=None, context_id=None, timeout_seconds=None, file_ids=None):
        return {
            "id": "t1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"kind": "data", "data": {"answer": 42}}]}],
        }

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(cli.client, "send_message", fake_send)
    args = argparse.Namespace(
        a2a_command="send",
        agent="http://remote.test",
        message="hello",
        file_id=[],
        token=None,
        json=True,
    )
    assert a2a_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"]["artifacts"][0]["parts"][0]["data"] == {"answer": 42}


def test_files_attach_artifact_json_and_task_lookup_preserve_file_metadata(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    from hermes_a2a_bridge.config import save_config
    from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
    from hermes_a2a_bridge.store import Store

    save_config(config)
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    ingest_args = argparse.Namespace(
        a2a_command="files",
        files_command="ingest",
        path=str(source),
        task_id=None,
        artifact_id=None,
        name=None,
        mime_type=None,
        metadata_json='{"purpose":"attach"}',
        json=True,
    )
    assert a2a_command(ingest_args) == 0
    file_id = json.loads(capsys.readouterr().out)["file"]["fileId"]

    store = Store(tmp_path / "a2a" / "tasks.sqlite3")
    message = Message(role="user", parts=[{"text": "ready"}])
    store.insert_task(
        Task(id="task-1", status=TaskStatus(state=TaskState.COMPLETED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    attach_args = argparse.Namespace(
        a2a_command="files",
        files_command="attach-artifact",
        file_id=file_id,
        task_id="task-1",
        artifact_id="artifact-file",
        name="Report",
        json=True,
    )
    assert a2a_command(attach_args) == 0
    attached = json.loads(capsys.readouterr().out)
    assert attached["artifact"]["parts"][0]["file"]["fileId"] == file_id
    assert attached["artifact"]["parts"][0]["metadata"] == {"purpose": "attach"}
    assert "storage_path" not in json.dumps(attached)

    task_args = argparse.Namespace(a2a_command="task", task_id="task-1", agent=None, token=None, json=True)
    assert a2a_command(task_args) == 0
    task_payload = json.loads(capsys.readouterr().out)
    assert task_payload["task"]["artifacts"][0]["parts"][0]["file"]["fileId"] == file_id
    assert "storage_path" not in json.dumps(task_payload)

    human_args = argparse.Namespace(a2a_command="task", task_id="task-1", agent=None, token=None, json=False)
    assert a2a_command(human_args) == 0
    human = capsys.readouterr().out
    assert "[file: report.txt, text/plain, 5 bytes" in human
    assert str(tmp_path) not in human


def test_files_attach_artifact_accepts_remote_url_reference(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    from hermes_a2a_bridge.config import save_config
    from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
    from hermes_a2a_bridge.store import Store

    save_config(config)
    add_args = argparse.Namespace(
        a2a_command="files",
        files_command="add-url",
        url="https://example.test/report.pdf?token=secret",
        name="report.pdf",
        mime_type="application/pdf",
        size_bytes=None,
        sha256=None,
        task_id=None,
        artifact_id=None,
        metadata_json='{"kind":"remote"}',
        json=True,
    )
    assert a2a_command(add_args) == 0
    file_id = json.loads(capsys.readouterr().out)["file"]["fileId"]

    store = Store(tmp_path / "a2a" / "tasks.sqlite3")
    message = Message(role="user", parts=[{"text": "ready"}])
    store.insert_task(
        Task(id="task-remote", status=TaskStatus(state=TaskState.COMPLETED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    attach_args = argparse.Namespace(
        a2a_command="files",
        files_command="attach-artifact",
        file_id=file_id,
        task_id="task-remote",
        artifact_id="artifact-remote",
        name=None,
        json=True,
    )
    assert a2a_command(attach_args) == 0
    attached = json.loads(capsys.readouterr().out)
    part = attached["artifact"]["parts"][0]
    assert part["file"]["metadataOnly"] is True
    assert part["file"]["bytesAvailable"] is False
    assert part["file"]["sourceUrl"] == "https://example.test/report.pdf"
    assert "uri" not in part["file"]
    serialized = json.dumps(attached)
    assert "token=secret" not in serialized
    assert "storage_path" not in serialized


def test_files_attach_artifact_rejects_unknown_ids(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    from hermes_a2a_bridge.config import save_config
    from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
    from hermes_a2a_bridge.store import Store

    save_config(config)
    store = Store(tmp_path / "a2a" / "tasks.sqlite3")
    message = Message(role="user", parts=[{"text": "ready"}])
    store.insert_task(
        Task(id="task-1", status=TaskStatus(state=TaskState.COMPLETED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    args = argparse.Namespace(
        a2a_command="files",
        files_command="attach-artifact",
        file_id="file_missingabcdefghijklmn",
        task_id="task-1",
        artifact_id=None,
        name=None,
        json=True,
    )
    assert a2a_command(args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "file_not_found"

    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    ingest_args = argparse.Namespace(
        a2a_command="files",
        files_command="ingest",
        path=str(source),
        task_id=None,
        artifact_id=None,
        name=None,
        mime_type=None,
        metadata_json=None,
        json=True,
    )
    assert a2a_command(ingest_args) == 0
    args.file_id = json.loads(capsys.readouterr().out)["file"]["fileId"]
    args.task_id = "missing-task"
    assert a2a_command(args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "task_not_found"


def test_subscribe_last_event_id_passes_through_with_prose_free_json(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    seen = {}

    async def fake_card(url):
        return {"name": "Remote", "url": "http://remote.test"}

    async def fake_subscribe(base, task_id, token=None, last_event_id=None):
        seen["last_event_id"] = last_event_id
        yield {"id": 13, "event": "message", "data": {"statusUpdate": {"taskId": task_id}}}

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(cli.client, "subscribe_task", fake_subscribe)
    args = argparse.Namespace(
        a2a_command="subscribe",
        task_id="t1",
        agent="http://remote.test",
        token=None,
        last_event_id=12,
        json=True,
    )
    assert a2a_command(args) == 0
    assert seen["last_event_id"] == 12
    assert json.loads(capsys.readouterr().out) == {
        "id": 13, "event": "message", "data": {"statusUpdate": {"taskId": "t1"}},
    }


def test_send_and_stream_file_id_flags_exist_but_file_flags_do_not():
    parser = argparse.ArgumentParser()
    cli.register_cli(parser)
    sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    send = sub.choices["send"]
    stream = sub.choices["stream"]

    assert "--file-id" in send.format_help()
    assert "--file-id" in stream.format_help()
    assert "--file " not in send.format_help()
    assert "--file " not in stream.format_help()

    parsed_send = parser.parse_args([
        "send", "http://remote.test", "hello",
        "--file-id", "file_abcdefghijklmnopqrstuv",
        "--file-id", "file_bcdefghijklmnopqrstuvw",
    ])
    assert parsed_send.file_id == [
        "file_abcdefghijklmnopqrstuv",
        "file_bcdefghijklmnopqrstuvw",
    ]

    with pytest.raises(SystemExit):
        parser.parse_args(["send", "http://remote.test", "hello", "--file", "report.txt"])
    with pytest.raises(SystemExit):
        parser.parse_args(["stream", "http://remote.test", "hello", "--file", "report.txt"])


def test_cli_send_file_id_passes_ids_without_reading_files(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    seen = {}

    async def fake_card(url):
        seen["card"] = url
        return {"name": "Remote", "url": "http://remote.test"}

    async def fake_send(base, message, token=None, context_id=None, timeout_seconds=None, file_ids=None):
        seen["send"] = {
            "base": base,
            "message": message,
            "token": token,
            "file_ids": list(file_ids or []),
        }
        return {
            "id": "t1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {
                "inputFileReferences": [{
                    "fileId": "file_abcdefghijklmnopqrstuv",
                    "name": "report.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": 5,
                    "bytesAvailable": True,
                }]
            },
        }

    def fail_read_bytes(*args, **kwargs):
        raise AssertionError("CLI --file-id must not read local file bytes")

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(cli.client, "send_message", fake_send)
    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    args = argparse.Namespace(
        a2a_command="send",
        agent="http://remote.test",
        message="analyze this",
        file_id=["file_abcdefghijklmnopqrstuv"],
        token="test-secret-token",
        json=False,
    )
    assert a2a_command(args) == 0
    output = capsys.readouterr().out
    assert seen["send"]["file_ids"] == ["file_abcdefghijklmnopqrstuv"]
    assert "[file: report.txt, text/plain, 5 bytes, id file_abcdefghijklmnopqrstuv]" in output
    assert str(tmp_path) not in output
    assert "storage_path" not in output
    assert "hello" not in output
    assert "test-secret-token" not in output


def test_cli_stream_file_id_passes_ids(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    seen = {}

    async def fake_card(url):
        return {"name": "Remote", "url": "http://remote.test"}

    async def fake_stream(base, message, token=None, context_id=None, file_ids=None):
        seen["file_ids"] = list(file_ids or [])
        yield {"id": 1, "event": "message", "data": {"task": {"id": "t1", "status": {"state": "TASK_STATE_SUBMITTED"}}}}

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(cli.client, "stream_message", fake_stream)
    args = argparse.Namespace(
        a2a_command="stream",
        agent="http://remote.test",
        message="stream this",
        file_id=["file_abcdefghijklmnopqrstuv"],
        token=None,
        json=True,
    )
    assert a2a_command(args) == 0
    assert seen["file_ids"] == ["file_abcdefghijklmnopqrstuv"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["task"]["id"] == "t1"


@pytest.mark.parametrize("value", [
    "file_short",
    r"C:\Users\asimo\report.txt",
    "https://example.test/report.pdf",
])
def test_cli_file_id_invalid_values_fail_before_remote_calls(config, tmp_path, monkeypatch, capsys, value):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))

    async def fail_card(*args, **kwargs):
        raise AssertionError("invalid --file-id should fail before discovery")

    monkeypatch.setattr(cli.client, "fetch_agent_card", fail_card)
    args = argparse.Namespace(
        a2a_command="send",
        agent="http://remote.test",
        message="hello",
        file_id=[value],
        token=None,
        json=True,
    )
    assert a2a_command(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "invalid_file_id"
    assert "test-secret-token" not in json.dumps(payload)


def test_cli_closed_gate_rejection_is_structured(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    payload = {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "Unsupported message: File parts are not supported yet.",
            "details": [{
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "INVALID_REQUEST",
                "domain": "a2a-protocol.org",
                "metadata": {"bridgeCode": "unsupported_part_type"},
            }],
        }
    }

    async def fake_card(url):
        return {"name": "Remote", "url": "http://remote.test"}

    async def fake_send(*args, **kwargs):
        raise ClientError("Remote agent error (HTTP 400): Unsupported message", status=400, payload=payload)

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(cli.client, "send_message", fake_send)
    args = argparse.Namespace(
        a2a_command="send",
        agent="http://remote.test",
        message="hello",
        file_id=["file_abcdefghijklmnopqrstuv"],
        token="test-secret-token",
        json=True,
    )
    assert a2a_command(args) == 1
    assert json.loads(capsys.readouterr().out) == payload


@pytest.mark.parametrize(
    "maintenance_command",
    ["stats", "prune-events", "recover-stale", "leases", "cancellations", "recover-leases"],
)
def test_maintenance_json_commands_are_prose_free(
    maintenance_command, config, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config
    from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
    from hermes_a2a_bridge.store import Store

    config["retention"]["max_events_per_task"] = 1
    save_config(config)
    store = Store(tmp_path / "a2a" / "tasks.sqlite3")
    message = Message(role="user", parts=[{"text": "stale"}])
    store.insert_task(
        Task(id="stale", status=TaskStatus(state=TaskState.WORKING), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    store.acquire_task_lease("stale", "expired-owner", 123, 60)
    store.create_cancellation_request(
        "stale", "requester", "expired-owner", 60, reason="test-secret-token must stay hidden",
    )
    store.add_task_event("stale", {"n": 1})
    store.add_task_event("stale", {"n": 2})
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE id='stale'")
        conn.execute(
            "UPDATE task_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id='stale'"
        )

    args = argparse.Namespace(
        a2a_command="maintenance", maintenance_command=maintenance_command, json=True,
    )
    assert a2a_command(args) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert isinstance(payload, dict)
    assert "test-secret-token" not in output
    if maintenance_command == "stats":
        assert {"task_count", "event_count", "retention", "recovery", "ownership", "sqlite"} <= set(payload)
    elif maintenance_command == "prune-events":
        assert payload["deleted_count"] == 1
    elif maintenance_command in {"recover-stale", "recover-leases"}:
        assert payload["recovered_count"] == 1
        if maintenance_command == "recover-stale":
            assert payload["expired_lease_recovery"]["recovered_count"] == 1
            assert "time_based_recovery" in payload
    elif maintenance_command == "leases":
        assert {"lease_count", "expired_lease_count", "leases"} <= set(payload)
        assert "heartbeat_age_seconds" in payload["leases"][0]
        assert "seconds_until_expiry" in payload["leases"][0]
    else:
        assert {"cancellation_count", "pending_cancellation_count", "cancellations"} <= set(payload)
        assert "reason" not in payload["cancellations"][0]


def test_maintenance_human_output_is_readable(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    args = argparse.Namespace(a2a_command="maintenance", maintenance_command="stats", json=False)
    assert a2a_command(args) == 0
    output = capsys.readouterr().out
    assert "Tasks:" in output
    assert "Events:" in output
    assert "Registry entries:" in output
    assert "SQLite:" in output
    assert "Leases:" in output


def _save_file_cli_config(config, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    config["files"]["storage_dir"] = str(tmp_path / "controlled-storage")
    from hermes_a2a_bridge.config import save_config

    save_config(config)


def test_files_ingest_list_show_delete_and_stats_json(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")

    ingest_args = argparse.Namespace(
        a2a_command="files",
        files_command="ingest",
        path=str(source),
        task_id="task-1",
        artifact_id="artifact-1",
        name="../../custom name.txt",
        mime_type=None,
        metadata_json='{"kind":"note"}',
        json=True,
    )
    assert a2a_command(ingest_args) == 0
    ingest_payload = json.loads(capsys.readouterr().out)
    file_id = ingest_payload["file"]["fileId"]
    assert ingest_payload["file"]["name"] == "custom_name.txt"
    assert ingest_payload["file"]["metadata"] == {"kind": "note"}
    assert "storage_path" not in json.dumps(ingest_payload)
    assert str(tmp_path) not in json.dumps(ingest_payload)

    list_args = argparse.Namespace(
        a2a_command="files", files_command="list", task_id="task-1", artifact_id=None, limit=10, json=True,
    )
    assert a2a_command(list_args) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["fileId"] for item in listed["files"]] == [file_id]
    assert "storage_path" not in json.dumps(listed)

    show_args = argparse.Namespace(a2a_command="files", files_command="show", file_id=file_id, json=True)
    assert a2a_command(show_args) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["file"]["fileId"] == file_id
    assert shown["file"]["bytesPresent"] is True
    assert "storage_path" not in json.dumps(shown)

    stats_args = argparse.Namespace(a2a_command="files", files_command="stats", json=True)
    assert a2a_command(stats_args) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["file_attachment_count"] == 1
    assert stats["file_attachment_bytes"] == 5
    assert str(tmp_path) not in json.dumps(stats)

    delete_args = argparse.Namespace(
        a2a_command="files", files_command="delete", file_id=file_id, delete_bytes=True, json=True,
    )
    assert a2a_command(delete_args) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["metadataDeleted"] is True
    assert deleted["bytesDeleted"] is True

    assert a2a_command(show_args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "file_not_found"


def test_files_verify_scan_cleanup_and_repair_json_are_prose_free(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    ingest_args = argparse.Namespace(
        a2a_command="files",
        files_command="ingest",
        path=str(source),
        task_id=None,
        artifact_id=None,
        name=None,
        mime_type=None,
        metadata_json=None,
        json=True,
    )
    assert a2a_command(ingest_args) == 0
    file_id = json.loads(capsys.readouterr().out)["file"]["fileId"]

    verify_args = argparse.Namespace(a2a_command="files", files_command="verify", file_id=file_id, json=True)
    assert a2a_command(verify_args) == 0
    verify = json.loads(capsys.readouterr().out)
    assert verify["status"] == "ok"
    assert "storage_path" not in json.dumps(verify)
    assert str(tmp_path) not in json.dumps(verify)

    root = Path(config["files"]["storage_dir"])
    orphan = root / "aa" / "orphan" / "content"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("orphan", encoding="utf-8")

    scan_args = argparse.Namespace(a2a_command="files", files_command="scan", json=True)
    assert a2a_command(scan_args) == 0
    scan = json.loads(capsys.readouterr().out)
    assert scan["orphanedByteCount"] == 1
    assert "storage_path" not in json.dumps(scan)

    cleanup_dry = argparse.Namespace(
        a2a_command="files", files_command="cleanup-orphans", dry_run=True, confirm=False, json=True,
    )
    assert a2a_command(cleanup_dry) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dryRun"] is True
    assert dry["deletedByteCount"] == 0
    assert orphan.exists()

    cleanup_confirm = argparse.Namespace(
        a2a_command="files", files_command="cleanup-orphans", dry_run=False, confirm=True, json=True,
    )
    assert a2a_command(cleanup_confirm) == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["dryRun"] is False
    assert confirmed["deletedByteCount"] == 1
    assert not orphan.exists()

    from hermes_a2a_bridge.config import database_path
    from hermes_a2a_bridge.store import Store

    row = Store(database_path()).get_file_attachment(file_id)
    Path(row["storage_path"]).unlink()

    repair_dry = argparse.Namespace(a2a_command="files", files_command="repair", dry_run=True, confirm=False, json=True)
    assert a2a_command(repair_dry) == 0
    repair_preview = json.loads(capsys.readouterr().out)
    assert repair_preview["metadataRepair"]["missingMetadataCount"] == 1

    repair_confirm = argparse.Namespace(
        a2a_command="files", files_command="repair", dry_run=False, confirm=True, json=True,
    )
    assert a2a_command(repair_confirm) == 0
    repair = json.loads(capsys.readouterr().out)
    assert repair["metadataRepair"]["removedFileIds"] == [file_id]
    assert str(tmp_path) not in json.dumps(repair)


def test_files_cleanup_and_repair_flag_conflicts_are_structured(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    cleanup = argparse.Namespace(
        a2a_command="files", files_command="cleanup-orphans", dry_run=True, confirm=True, json=True,
    )
    assert a2a_command(cleanup) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_cleanup_flags"

    repair = argparse.Namespace(a2a_command="files", files_command="repair", dry_run=True, confirm=True, json=True)
    assert a2a_command(repair) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_repair_flags"


def test_files_maintenance_human_output_does_not_expose_paths(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    root = Path(config["files"]["storage_dir"])
    orphan = root / "aa" / "orphan" / "content"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("orphan", encoding="utf-8")

    for command in ("scan", "cleanup-orphans", "repair"):
        args = argparse.Namespace(
            a2a_command="files",
            files_command=command,
            dry_run=True,
            confirm=False,
            json=False,
        )
        if command == "scan":
            delattr(args, "dry_run")
            delattr(args, "confirm")
        assert a2a_command(args) == 0
        output = capsys.readouterr().out
        assert str(tmp_path) not in output
        assert str(root) not in output


def test_files_add_url_json_show_and_human_output_are_safe(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    args = argparse.Namespace(
        a2a_command="files",
        files_command="add-url",
        url="https://user:pass@example.test/report.pdf?token=secret#frag",
        name="report.pdf",
        mime_type="application/pdf",
        size_bytes=123,
        sha256="b" * 64,
        task_id="task-1",
        artifact_id="artifact-1",
        metadata_json='{"purpose":"remote"}',
        json=True,
    )
    assert a2a_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    file_id = payload["file"]["fileId"]
    assert payload["file"]["source"] == "remote_url"
    assert payload["file"]["metadataOnly"] is True
    assert payload["file"]["sourceUrl"] == "https://example.test/report.pdf"
    serialized = json.dumps(payload)
    assert "user:pass" not in serialized
    assert "token=secret" not in serialized
    assert "storage_path" not in serialized

    show_args = argparse.Namespace(a2a_command="files", files_command="show", file_id=file_id, json=True)
    assert a2a_command(show_args) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["file"]["bytesPresent"] is False
    assert shown["file"]["metadataOnly"] is True

    args.json = False
    args.url = "https://example.test/other.pdf"
    assert a2a_command(args) == 0
    human = capsys.readouterr().out
    assert "metadata-only URL reference" in human
    assert "token=secret" not in human


@pytest.mark.parametrize("url", [
    "file:///tmp/report.pdf",
    "ftp://example.test/report.pdf",
    "data:text/plain,hello",
    "javascript:alert(1)",
    r"C:\Users\asimo\report.pdf",
    "report.pdf",
])
def test_files_add_url_rejects_unsupported_schemes_and_paths(config, tmp_path, monkeypatch, capsys, url):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    args = argparse.Namespace(
        a2a_command="files",
        files_command="add-url",
        url=url,
        name="report.pdf",
        mime_type="application/pdf",
        size_bytes=None,
        sha256=None,
        task_id=None,
        artifact_id=None,
        metadata_json=None,
        json=True,
    )
    assert a2a_command(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "unsupported_remote_url"
    assert "test-secret-token" not in json.dumps(payload)


def test_files_delete_without_delete_bytes_refuses_existing_bytes(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    ingest_args = argparse.Namespace(
        a2a_command="files",
        files_command="ingest",
        path=str(source),
        task_id=None,
        artifact_id=None,
        name=None,
        mime_type=None,
        metadata_json=None,
        json=True,
    )
    assert a2a_command(ingest_args) == 0
    file_id = json.loads(capsys.readouterr().out)["file"]["fileId"]

    delete_args = argparse.Namespace(
        a2a_command="files", files_command="delete", file_id=file_id, delete_bytes=False, json=True,
    )
    assert a2a_command(delete_args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "delete_bytes_required"
    assert "storage_path" not in json.dumps(payload)


def test_files_invalid_metadata_json_and_missing_file_are_structured(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    bad_args = argparse.Namespace(
        a2a_command="files",
        files_command="ingest",
        path=str(tmp_path / "missing.txt"),
        task_id=None,
        artifact_id=None,
        name=None,
        mime_type=None,
        metadata_json="{",
        json=True,
    )
    assert a2a_command(bad_args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_metadata_json"

    bad_args.metadata_json = None
    assert a2a_command(bad_args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "local_file_not_found"
    assert "test-secret-token" not in json.dumps(payload)


def test_files_human_output_does_not_expose_storage_or_source_paths(config, tmp_path, monkeypatch, capsys):
    _save_file_cli_config(config, tmp_path, monkeypatch)
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    args = argparse.Namespace(
        a2a_command="files",
        files_command="ingest",
        path=str(source),
        task_id=None,
        artifact_id=None,
        name=None,
        mime_type=None,
        metadata_json=None,
        json=False,
    )
    assert a2a_command(args) == 0
    output = capsys.readouterr().out
    assert "Stored file_" in output
    assert str(tmp_path) not in output
    assert str(Path(config["files"]["storage_dir"])) not in output


def test_files_remote_fetch_metadata_and_download_commands(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    seen = {}

    async def fake_card(url):
        return {"name": "Remote", "url": "http://remote.test"}

    async def fake_metadata(base, file_id, token=None):
        seen["metadata"] = (base, file_id, token)
        return {
            "fileId": file_id,
            "name": "report.txt",
            "mimeType": "text/plain",
            "sizeBytes": 5,
            "sha256": "x" * 64,
        }

    async def fake_download(base, file_id, token=None, output_path=None):
        seen["download"] = (base, file_id, token, str(output_path))
        Path(output_path).write_bytes(b"hello")
        return b"hello"

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(cli.client, "get_file_metadata", fake_metadata)
    monkeypatch.setattr(cli.client, "download_file", fake_download)

    metadata_args = argparse.Namespace(
        a2a_command="files",
        files_command="fetch-metadata",
        file_id="file_abcdefghijklmnopqrstuv",
        agent="http://remote.test",
        token="test-secret-token",
        json=True,
    )
    assert a2a_command(metadata_args) == 0
    metadata_payload = json.loads(capsys.readouterr().out)
    assert metadata_payload["file"]["fileId"] == "file_abcdefghijklmnopqrstuv"
    assert "storage_path" not in json.dumps(metadata_payload)
    assert seen["metadata"] == ("http://remote.test", "file_abcdefghijklmnopqrstuv", "test-secret-token")

    output = tmp_path / "download.txt"
    download_args = argparse.Namespace(
        a2a_command="files",
        files_command="download",
        file_id="file_abcdefghijklmnopqrstuv",
        output_path=str(output),
        agent="http://remote.test",
        token="test-secret-token",
        json=True,
    )
    assert a2a_command(download_args) == 0
    download_payload = json.loads(capsys.readouterr().out)
    assert download_payload["bytesWritten"] == 5
    assert output.read_bytes() == b"hello"
    assert "test-secret-token" not in json.dumps(download_payload)


def test_subscribe_replay_gap_json_is_structured_and_prose_free(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)
    payload = {
        "success": False,
        "error": "Requested replay cursor is no longer available because event history was pruned.",
        "code": "replay_gap",
        "task_id": "t1",
        "last_event_id": 1,
        "oldest_available_event_id": 5,
    }

    async def fake_subscribe(*args, **kwargs):
        raise ClientError("Replay cursor expired", status=409, payload=payload)
        yield

    monkeypatch.setattr(cli.client, "subscribe_task", fake_subscribe)
    args = argparse.Namespace(
        a2a_command="subscribe", task_id="t1", agent=None, token=None,
        last_event_id=1, json=True,
    )
    assert a2a_command(args) == 1
    assert json.loads(capsys.readouterr().out) == payload


def test_cli_send_0_3_only_peer_json_is_structured(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_card(url):
        return {
            "name": "Legacy 0.3 Peer",
            "url": "http://remote.test/v1",
            "protocolVersion": "0.3",
        }

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    args = argparse.Namespace(
        a2a_command="send",
        agent="http://remote.test",
        message="hello",
        token=None,
        json=True,
    )
    assert a2a_command(args) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is False
    assert payload["code"] == "unsupported_protocol_version"
    assert payload["protocol_version"] == "0.3"
    assert "A2A 0.3 REST behavior" in payload["error"]
    assert captured.err == ""


def test_cli_send_0_3_only_peer_human_error_is_clear(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_card(url):
        return {
            "name": "Legacy 0.3 Peer",
            "url": "http://remote.test/v1",
            "protocolVersion": "0.3",
        }

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    args = argparse.Namespace(
        a2a_command="send",
        agent="http://remote.test",
        message="hello",
        token=None,
        json=False,
    )
    assert a2a_command(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsupported 0.3" in captured.err or "A2A 0.3 REST behavior" in captured.err


def test_cli_stream_0_3_only_peer_json_is_structured(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_card(url):
        return {
            "name": "Legacy 0.3 Peer",
            "url": "http://remote.test/v1",
            "protocolVersion": "0.3",
        }

    monkeypatch.setattr(cli.client, "fetch_agent_card", fake_card)
    args = argparse.Namespace(
        a2a_command="stream",
        agent="http://remote.test",
        message="hello",
        token=None,
        json=True,
    )
    assert a2a_command(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "unsupported_protocol_version"
    assert payload["protocol_version"] == "0.3"
