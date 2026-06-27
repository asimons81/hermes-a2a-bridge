import json
import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_a2a_bridge import files
from hermes_a2a_bridge.errors import ExecutorError
from hermes_a2a_bridge.models import Message, Task, TaskState, TaskStatus
from hermes_a2a_bridge.operations import add_remote_url_file_reference, attach_file_artifact, ingest_local_file
from hermes_a2a_bridge.server import EVENT_BROKER_KEY, EXECUTOR_MANAGER_KEY, create_app
from hermes_a2a_bridge.store import Store


@pytest.fixture
async def server_client(config, tmp_path):
    store = Store(tmp_path / "server.sqlite3")
    client = TestClient(TestServer(create_app(config, store)))
    await client.start_server()
    yield client, store
    await client.close()


async def test_public_routes_need_no_auth(server_client):
    client, _ = server_client
    assert (await client.get("/health")).status == 200
    response = await client.get("/.well-known/agent-card.json")
    assert response.status == 200
    assert "auth_token" not in str(await response.json())


def test_server_route_list_has_only_expected_file_routes(config, tmp_path):
    app = create_app(config, Store(tmp_path / "routes.sqlite3"))
    routes = sorted((route.method, route.resource.canonical) for route in app.router.routes())
    assert routes == [
        ("GET", "/.well-known/agent-card.json"),
        ("GET", "/files/{file_id}"),
        ("GET", "/files/{file_id}/metadata"),
        ("GET", "/health"),
        ("GET", "/tasks"),
        ("GET", "/tasks/{task_id}"),
        ("HEAD", "/.well-known/agent-card.json"),
        ("HEAD", "/files/{file_id}"),
        ("HEAD", "/files/{file_id}/metadata"),
        ("HEAD", "/health"),
        ("HEAD", "/tasks"),
        ("HEAD", "/tasks/{task_id}"),
        ("POST", "/message:send"),
        ("POST", "/message:stream"),
        ("POST", "/tasks/{task_id}:cancel"),
        ("POST", "/tasks/{task_id}:subscribe"),
    ]
    assert sorted(path for _, path in routes if path.startswith("/files")) == [
        "/files/{file_id}",
        "/files/{file_id}",
        "/files/{file_id}/metadata",
        "/files/{file_id}/metadata",
    ]


async def test_message_requires_auth(server_client):
    client, _ = server_client
    assert (await client.post("/message:send", json={})).status == 401


def _stage_file(tmp_path, config, store, content: bytes = b"hello") -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config["files"]["storage_dir"] = str(tmp_path / "controlled-storage")
    source = tmp_path / "report.txt"
    source.write_bytes(content)
    return ingest_local_file(
        source,
        store,
        config,
        name="../../report final.txt",
        metadata={"purpose": "route-test"},
    )["file"]["fileId"]


async def test_file_metadata_route_auth_and_safe_payload(server_client, config, tmp_path):
    client, store = server_client
    file_id = _stage_file(tmp_path, config, store)
    missing = await client.get(f"/files/{file_id}/metadata")
    bad = await client.get(f"/files/{file_id}/metadata", headers={"Authorization": "Bearer wrong-token"})
    ok = await client.get(
        f"/files/{file_id}/metadata",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    unknown = await client.get(
        "/files/file_unknownabcdefghijklmnop/metadata",
        headers={"Authorization": "Bearer test-secret-token"},
    )

    assert missing.status == 401
    assert bad.status == 401
    assert unknown.status == 404
    payload = await ok.json()
    assert ok.status == 200
    assert payload["success"] is True
    assert payload["file"]["fileId"] == file_id
    assert payload["file"]["name"] == "report_final.txt"
    assert payload["file"]["mimeType"] == "text/plain"
    assert payload["file"]["sizeBytes"] == 5
    assert payload["file"]["metadata"] == {"purpose": "route-test"}
    text = json.dumps(payload)
    assert "storage_path" not in text
    assert str(tmp_path) not in text
    assert "test-secret-token" not in text


async def test_file_byte_route_auth_headers_and_body(server_client, config, tmp_path):
    client, store = server_client
    file_id = _stage_file(tmp_path, config, store, b"download me")
    missing = await client.get(f"/files/{file_id}")
    bad = await client.get(f"/files/{file_id}", headers={"Authorization": "Bearer wrong-token"})
    ok = await client.get(f"/files/{file_id}", headers={"Authorization": "Bearer test-secret-token"})

    assert missing.status == 401
    assert bad.status == 401
    assert ok.status == 200
    assert await ok.read() == b"download me"
    assert ok.headers["Content-Type"].startswith("text/plain")
    assert ok.headers["Content-Length"] == str(len(b"download me"))
    assert ok.headers["Content-Disposition"].startswith('attachment; filename="report_final.txt"')
    assert ok.headers["Cache-Control"] == "no-store"
    assert "storage" not in ok.headers["Content-Disposition"].lower()


async def test_remote_url_metadata_route_and_byte_route_unavailable(server_client, config):
    client, store = server_client
    file_id = add_remote_url_file_reference(
        "https://user:pass@example.test/report.pdf?token=secret#frag",
        store,
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
    )["file"]["fileId"]

    metadata = await client.get(
        f"/files/{file_id}/metadata",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    assert metadata.status == 200
    payload = await metadata.json()
    assert payload["file"]["source"] == "remote_url"
    assert payload["file"]["metadataOnly"] is True
    assert payload["file"]["sourceUrl"] == "https://example.test/report.pdf"
    serialized = json.dumps(payload)
    assert "user:pass" not in serialized
    assert "token=secret" not in serialized
    assert "storage_path" not in serialized

    byte_response = await client.get(
        f"/files/{file_id}",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    body = await byte_response.json()
    assert byte_response.status == 409
    assert body["code"] == "file_bytes_unavailable"
    assert "metadata-only remote URL reference" in body["error"]
    assert "token=secret" not in json.dumps(body)


async def test_file_byte_route_missing_bytes_unsafe_path_bad_id_and_checksum(server_client, config, tmp_path):
    client, store = server_client
    file_id = _stage_file(tmp_path, config, store, b"hello")
    row = store.get_file_attachment(file_id)
    Path(row["storage_path"]).unlink()
    missing = await client.get(f"/files/{file_id}", headers={"Authorization": "Bearer test-secret-token"})
    assert missing.status == 410
    missing_payload = await missing.json()
    assert missing_payload["code"] == "file_bytes_missing"
    assert "storage_path" not in json.dumps(missing_payload)

    bad_id = await client.get("/files/bad$id", headers={"Authorization": "Bearer test-secret-token"})
    assert bad_id.status == 400
    assert (await bad_id.json())["code"] == "invalid_file_id"

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    unsafe_id = "file_unsafeabcdefghijklmnopq"
    store.add_file_attachment(
        file_id=unsafe_id,
        filename="outside.txt",
        safe_filename="outside.txt",
        mime_type="text/plain",
        size_bytes=7,
        sha256=files.sha256_file(outside),
        storage_path=str(outside),
        source="local_cli",
    )
    unsafe = await client.get(f"/files/{unsafe_id}", headers={"Authorization": "Bearer test-secret-token"})
    assert unsafe.status == 403
    assert (await unsafe.json())["code"] == "unsafe_file_path"

    mismatch_id = _stage_file(tmp_path / "mismatch", config, store, b"original")
    mismatch_row = store.get_file_attachment(mismatch_id)
    Path(mismatch_row["storage_path"]).write_bytes(b"modified")
    mismatch = await client.get(
        f"/files/{mismatch_id}",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    assert mismatch.status == 409
    assert (await mismatch.json())["code"] == "file_checksum_mismatch"

    size_id = _stage_file(tmp_path / "size-mismatch", config, store, b"original")
    size_row = store.get_file_attachment(size_id)
    Path(size_row["storage_path"]).write_bytes(b"changed-size")
    size_mismatch = await client.get(
        f"/files/{size_id}",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    assert size_mismatch.status == 409
    assert (await size_mismatch.json())["code"] == "file_size_mismatch"


async def test_file_byte_route_rejects_symlink_escape_if_supported(server_client, config, tmp_path):
    client, store = server_client
    config["files"]["storage_dir"] = str(tmp_path / "controlled-storage")
    root = files.resolve_storage_root(config)
    files.ensure_storage_root(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")
    file_id = "file_symlinkabcdefghijklmnop"
    store.add_file_attachment(
        file_id=file_id,
        filename="linked.txt",
        safe_filename="linked.txt",
        mime_type="text/plain",
        size_bytes=6,
        sha256=files.sha256_file(target),
        storage_path=str(link),
        source="local_cli",
    )
    response = await client.get(f"/files/{file_id}", headers={"Authorization": "Bearer test-secret-token"})
    assert response.status == 403
    assert (await response.json())["code"] == "unsafe_file_path"


async def test_non_text_part_rejected(server_client):
    client, _ = server_client
    response = await client.post(
        "/message:send", headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"kind": "file", "text": "no"}]}},
    )
    assert response.status == 400
    assert "Unsupported message" in (await response.json())["error"]


async def test_file_parts_still_rejected_even_if_config_flag_is_true(server_client, config):
    config["parts"]["allow_file_parts"] = True
    response = await server_client[0].post(
        "/message:send",
        headers={"Authorization": "Bearer test-secret-token", "A2A-Version": "1.0"},
        json={"message": {"role": "ROLE_USER", "parts": [{"file": {"name": "report.pdf"}}]}},
    )
    body = await response.json()
    assert response.status == 400
    assert body["error"]["details"][0]["metadata"]["bridgeCode"] == "file_reference_disabled"
    assert "disabled by configuration" in body["error"]["message"]


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        ("unsupported_file_part_request.json", "File parts are not supported yet"),
        ("malformed_unknown_part_request.json", "Unsupported part"),
    ],
)
async def test_unsupported_external_part_shapes_are_rejected_clearly(
    server_client, fixture_name, expected,
):
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "a2a" / fixture_name).read_text(encoding="utf-8")
    )
    response = await server_client[0].post(
        "/message:send", headers={"Authorization": "Bearer test-secret-token"}, json=payload,
    )
    body = await response.json()
    assert response.status == 400
    assert body["code"] == "unsupported_part_type"
    assert expected in body["error"]


async def test_a2a_json_content_type_is_accepted(server_client, monkeypatch):
    async def completed(prompt, config):
        return "ok"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    response = await server_client[0].post(
        "/message:send",
        headers={
            "Authorization": "Bearer test-secret-token",
            "Content-Type": "application/a2a+json; charset=utf-8",
        },
        data=json.dumps({
            "message": {
                "messageId": "external-1", "role": "ROLE_USER",
                "parts": [{"text": "hello", "mediaType": "text/plain"}],
            }
        }),
    )
    assert response.status == 200
    assert (await response.json())["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_unsupported_content_type_and_bad_json_are_json_errors(server_client):
    client, _ = server_client
    response = await client.post(
        "/message:send",
        headers={"Authorization": "Bearer test-secret-token", "Content-Type": "text/plain"},
        data="hello",
    )
    payload = await response.json()
    assert response.status == 415
    assert payload["success"] is False
    assert payload["code"] == "unsupported_content_type"

    response = await client.post(
        "/message:send",
        headers={"Authorization": "Bearer test-secret-token", "Content-Type": "application/json"},
        data="{",
    )
    payload = await response.json()
    assert response.status == 400
    assert payload["code"] == "malformed_json"


async def test_unavailable_verified_executor_creates_failed_task(server_client, monkeypatch):
    async def unavailable(prompt, config):
        raise ExecutorError("Hermes executor was not found: hermes")
    monkeypatch.setattr("hermes_a2a_bridge.server.execute", unavailable)
    client, _ = server_client
    response = await client.post(
        "/message:send", headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"kind": "text", "text": "hello"}]}},
    )
    task = await response.json()
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert "not found" in task["status"]["message"]["parts"][0]["text"]
    got = await client.get(f"/tasks/{task['id']}", headers={"Authorization": "Bearer test-secret-token"})
    assert got.status == 200
    listed = await client.get("/tasks", headers={"Authorization": "Bearer test-secret-token"})
    assert len(await listed.json()) == 1


async def test_cancel_submitted_task(server_client):
    client, store = server_client
    message = Message(role="user", parts=[{"text": "wait"}])
    task = Task(id="pending", status=TaskStatus(state=TaskState.SUBMITTED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    response = await client.post("/tasks/pending:cancel", headers={"Authorization": "Bearer test-secret-token"})
    assert (await response.json())["status"]["state"] == "TASK_STATE_CANCELED"


async def test_completed_task_cannot_be_canceled(server_client, monkeypatch):
    client, store = server_client
    async def must_not_cancel(*args, **kwargs):
        raise AssertionError("completed task cancellation reached the executor manager")
    monkeypatch.setattr(client.app[EXECUTOR_MANAGER_KEY], "cancel", must_not_cancel)
    message = Message(role="user", parts=[{"text": "done"}])
    task = Task(id="finished", status=TaskStatus(state=TaskState.COMPLETED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    store.update_task("finished", TaskState.COMPLETED)
    response = await client.post("/tasks/finished:cancel", headers={"Authorization": "Bearer test-secret-token"})
    payload = await response.json()
    assert response.status == 409
    assert payload["code"] == "task_not_cancelable"


def _sse_frames(body: str):
    frames = []
    for block in body.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            name, value = line.split(":", 1)
            fields[name] = value.lstrip()
        frames.append({"id": int(fields["id"]), "event": fields["event"], "data": json.loads(fields["data"])})
    return frames


def _sse_events(body: str):
    return [frame["data"] for frame in _sse_frames(body)]


async def test_message_stream_rejects_missing_auth_bad_json_and_non_text(server_client):
    client, _ = server_client
    assert (await client.post("/message:stream", json={})).status == 401

    bad_json = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token", "Content-Type": "application/json"},
        data="{",
    )
    assert bad_json.status == 400
    assert (await bad_json.json())["code"] == "malformed_json"

    non_text = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"kind": "file", "text": "no"}]}},
    )
    assert non_text.status == 400
    assert (await non_text.json())["code"] == "unsupported_part_type"

    sdk_style_file = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token", "A2A-Version": "1.0"},
        json={"message": {"role": "ROLE_USER", "parts": [{"file": {"name": "report.txt"}}]}},
    )
    body = await sdk_style_file.json()
    assert sdk_style_file.status == 400
    assert body["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"


async def test_data_only_message_send_validates_and_creates_task(server_client, monkeypatch):
    seen = {}

    async def completed(prompt, config):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    payload = {"message": {"role": "ROLE_USER", "parts": [{"kind": "data", "data": {"alpha": 1, "beta": [2]}}]}}
    response = await server_client[0].post(
        "/message:send", headers={"Authorization": "Bearer test-secret-token"}, json=payload,
    )
    task = await response.json()
    assert response.status == 200
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["history"][0]["parts"][0]["data"] == {"alpha": 1, "beta": [2]}
    assert "Data part 1:" in seen["prompt"]
    assert '"alpha": 1' in seen["prompt"]


async def test_mixed_text_and_data_message_send_renders_deterministically(server_client, monkeypatch):
    seen = {}

    async def completed(prompt, config):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    response = await server_client[0].post(
        "/message:send",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [
            {"text": "Summarize this"},
            {"type": "data", "data": [{"name": "Ada"}, {"name": "Grace"}], "metadata": {"source": "test"}},
        ]}},
    )
    task = await response.json()
    assert response.status == 200
    assert task["history"][0]["parts"][1]["metadata"] == {"source": "test"}
    assert seen["prompt"].startswith("Text: Summarize this")
    assert "Data part 1:" in seen["prompt"]
    assert '"name": "Ada"' in seen["prompt"]


async def test_data_only_stream_emits_data_artifact_and_replays(server_client, monkeypatch):
    async def completed(prompt, config):
        return '{"answer": 42, "items": [1, 2]}'

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    response = await server_client[0].post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"kind": "data", "data": {"question": "life"}}]}},
    )
    events = _sse_events(await response.text())
    task_id = events[0]["task"]["id"]
    artifact = events[2]["artifactUpdate"]["artifact"]
    assert "kind" not in artifact["parts"][0]
    assert artifact["parts"][0]["data"] == {"answer": 42, "items": [1, 2]}
    stored = server_client[1].list_task_events(task_id)
    assert stored[2].event["artifactUpdate"]["artifact"]["parts"][0]["data"]["answer"] == 42

    replay = await server_client[0].post(
        f"/tasks/{task_id}:subscribe",
        headers={"Authorization": "Bearer test-secret-token", "Last-Event-ID": str(stored[1].id)},
    )
    replay_events = _sse_events(await replay.text())
    assert replay_events[0]["artifactUpdate"]["artifact"]["parts"][0]["data"]["answer"] == 42


async def test_invalid_and_oversized_data_parts_are_rejected_clearly(server_client, config):
    invalid = await server_client[0].post(
        "/message:send",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"kind": "data", "data": "raw string"}]}},
    )
    invalid_body = await invalid.json()
    assert invalid.status == 400
    assert invalid_body["code"] == "unsupported_part_type"

    config["parts"]["max_data_part_bytes"] = 8
    oversized = await server_client[0].post(
        "/message:send",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"kind": "data", "data": {"too": "large"}}]}},
    )
    oversized_body = await oversized.json()
    assert oversized.status == 400
    assert oversized_body["code"] == "data_part_too_large"
    assert "test-secret-token" not in json.dumps(oversized_body)


async def test_invalid_executor_json_remains_text_artifact(server_client, monkeypatch):
    async def completed(prompt, config):
        return "{not json"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    response = await server_client[0].post(
        "/message:send",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"text": "hello"}]}},
    )
    task = await response.json()
    assert task["artifacts"][0]["parts"][0]["text"] == "{not json"
    assert "data" not in task["artifacts"][0]["parts"][0]


async def test_message_stream_emits_initial_artifact_and_completed_events(server_client, monkeypatch):
    async def completed(prompt, config):
        assert prompt == "hello"
        return "streamed result"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    client, _ = server_client
    response = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"text": "hello"}]}},
    )
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    events = _sse_events(await response.text())
    assert events[0]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert events[1]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
    assert events[2]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "streamed result"
    assert events[2]["artifactUpdate"]["lastChunk"] is True
    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert not any("kind" in event for event in events)
    stored = server_client[1].list_task_events(events[0]["task"]["id"])
    assert [event.id for event in stored] == sorted(event.id for event in stored)
    assert len(stored) == len(events)


async def test_message_stream_client_disconnect_releases_subscription(server_client, monkeypatch):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def controlled(prompt, config):
        started.set()
        await finish.wait()
        return "done"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", controlled)
    client, _ = server_client
    response = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "ROLE_USER", "parts": [{"text": "disconnect"}]}},
    )
    initial = _sse_frames((await response.content.readuntil(b"\n\n")).decode())[0]
    task_id = initial["data"]["task"]["id"]
    await asyncio.wait_for(started.wait(), timeout=1)
    response.close()
    finish.set()
    for _ in range(100):
        channel = client.app[EVENT_BROKER_KEY]._channels.get(task_id)
        if channel is None or not channel.subscribers:
            break
        await asyncio.sleep(0.01)
    channel = client.app[EVENT_BROKER_KEY]._channels.get(task_id)
    assert channel is None or not channel.subscribers


async def test_message_stream_null_executor_emits_clear_failed_event(server_client, config):
    config["executor"]["command"] = None
    client, _ = server_client
    response = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"text": "hello"}]}},
    )
    events = _sse_events(await response.text())
    final = events[-1]["statusUpdate"]
    assert final["status"]["state"] == "TASK_STATE_FAILED"
    assert final["status"]["message"]["parts"][0]["text"] == (
        "No Hermes executor command configured. Set executor.command in ~/.hermes/a2a/config.yaml"
    )


async def test_stream_redacts_tokens_from_events(server_client, monkeypatch):
    async def leaks(prompt, config):
        raise RuntimeError("failed with test-secret-token")

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", leaks)
    client, store = server_client
    response = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"text": "hello"}]}},
    )
    body = await response.text()
    assert "test-secret-token" not in body
    assert "[REDACTED]" in body
    task_id = _sse_frames(body)[0]["data"]["task"]["id"]
    assert "test-secret-token" not in json.dumps([event.event for event in store.list_task_events(task_id)])


async def test_subscribe_auth_missing_and_terminal_errors_are_json(server_client):
    client, store = server_client
    assert (await client.post("/tasks/anything:subscribe")).status == 401
    missing = await client.post(
        "/tasks/missing:subscribe", headers={"Authorization": "Bearer test-secret-token"},
    )
    assert missing.status == 404
    assert (await missing.json())["code"] == "task_not_found"

    message = Message(role="user", parts=[{"text": "done"}])
    task = Task(id="terminal", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    store.update_task("terminal", TaskState.COMPLETED)
    terminal = await client.post(
        "/tasks/terminal:subscribe", headers={"Authorization": "Bearer test-secret-token"},
    )
    assert terminal.status == 409
    assert terminal.headers["Content-Type"].startswith("application/json")
    assert (await terminal.json())["code"] == "no_new_events"


async def test_subscribe_emits_snapshot_and_future_terminal_update(server_client):
    client, store = server_client
    message = Message(role="user", parts=[{"text": "wait"}])
    task = Task(id="active", contextId="ctx", status=TaskStatus(state=TaskState.SUBMITTED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})

    response = await client.post(
        "/tasks/active:subscribe", headers={"Authorization": "Bearer test-secret-token"},
    )
    first = _sse_frames((await response.content.readuntil(b"\n\n")).decode())[0]
    assert first["data"]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"

    canceled = await client.post(
        "/tasks/active:cancel", headers={"Authorization": "Bearer test-secret-token"},
    )
    assert canceled.status == 200
    rest = _sse_events((await response.text()))
    assert rest[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_CANCELED"


async def test_subscribe_replays_only_events_after_last_event_id(server_client):
    client, store = server_client
    message = Message(role="user", parts=[{"text": "done"}])
    task = Task(id="replay", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    first_id = store.add_task_event("replay", {"task": {"id": "replay"}})
    second_id = store.add_task_event("replay", {
        "statusUpdate": {"taskId": "replay", "contextId": "ctx", "status": {"state": "TASK_STATE_COMPLETED"}},
    })
    response = await client.post(
        "/tasks/replay:subscribe",
        headers={"Authorization": "Bearer test-secret-token", "Last-Event-ID": str(first_id)},
    )
    frames = _sse_frames(await response.text())
    assert [frame["id"] for frame in frames] == [second_id]
    assert frames[0]["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_subscribe_replays_file_artifact_metadata(server_client, config, tmp_path):
    client, store = server_client
    config["files"]["storage_dir"] = str(tmp_path / "controlled-storage")
    config["server"]["public_url"] = "http://127.0.0.1:8765"
    message = Message(role="user", parts=[{"text": "done"}])
    task = Task(id="file-replay", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    first_id = store.add_task_event("file-replay", {"task": {"id": "file-replay"}})
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    file_id = ingest_local_file(source, store, config)["file"]["fileId"]
    result = attach_file_artifact(store, config, file_id, "file-replay", artifact_id="artifact-file")

    response = await client.post(
        "/tasks/file-replay:subscribe",
        headers={"Authorization": "Bearer test-secret-token", "Last-Event-ID": str(first_id)},
    )
    frames = _sse_frames(await response.text())
    part = frames[0]["data"]["artifactUpdate"]["artifact"]["parts"][0]
    assert frames[0]["id"] == result["eventId"]
    assert part["file"]["fileId"] == file_id
    assert part["file"]["uri"] == f"http://127.0.0.1:8765/files/{file_id}"
    serialized = json.dumps(frames)
    assert "hello" not in serialized
    assert "storage_path" not in serialized
    assert str(tmp_path) not in serialized


async def test_subscribe_replays_remote_url_file_artifact_metadata(server_client, config):
    client, store = server_client
    message = Message(role="user", parts=[{"text": "done"}])
    task = Task(id="remote-file-replay", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    first_id = store.add_task_event("remote-file-replay", {"task": {"id": "remote-file-replay"}})
    file_id = add_remote_url_file_reference(
        "https://example.test/report.pdf?token=secret",
        store,
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
    )["file"]["fileId"]
    result = attach_file_artifact(store, config, file_id, "remote-file-replay", artifact_id="artifact-remote")

    response = await client.post(
        "/tasks/remote-file-replay:subscribe",
        headers={"Authorization": "Bearer test-secret-token", "Last-Event-ID": str(first_id)},
    )
    frames = _sse_frames(await response.text())
    part = frames[0]["data"]["artifactUpdate"]["artifact"]["parts"][0]
    assert frames[0]["id"] == result["eventId"]
    assert part["file"]["fileId"] == file_id
    assert part["file"]["metadataOnly"] is True
    assert part["file"]["sourceUrl"] == "https://example.test/report.pdf"
    assert "uri" not in part["file"]
    serialized = json.dumps(frames)
    assert "token=secret" not in serialized
    assert "storage_path" not in serialized


async def test_live_subscribe_receives_file_artifact_update_from_sqlite_polling(server_client, config, tmp_path):
    client, store = server_client
    config["streaming"]["poll_interval_seconds"] = 0.01
    config["files"]["storage_dir"] = str(tmp_path / "controlled-storage")
    message = Message(role="user", parts=[{"text": "wait"}])
    task = Task(id="file-live", contextId="ctx", status=TaskStatus(state=TaskState.SUBMITTED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})

    response = await client.post(
        "/tasks/file-live:subscribe", headers={"Authorization": "Bearer test-secret-token"},
    )
    first = _sse_frames((await response.content.readuntil(b"\n\n")).decode())[0]
    assert first["data"]["task"]["id"] == "file-live"

    source = tmp_path / "live.txt"
    source.write_text("hello", encoding="utf-8")
    file_id = ingest_local_file(source, store, config)["file"]["fileId"]
    attach_file_artifact(store, config, file_id, "file-live")
    next_frame = _sse_frames((await response.content.readuntil(b"\n\n")).decode())[0]
    assert next_frame["data"]["artifactUpdate"]["artifact"]["parts"][0]["file"]["fileId"] == file_id
    response.close()


async def test_terminal_replay_survives_a_new_server_app(config, tmp_path):
    store = Store(tmp_path / "durable.sqlite3")
    message = Message(role="user", parts=[{"text": "done"}])
    task = Task(id="restart-replay", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), history=[message])
    store.insert_task(task, {"message": message.model_dump(by_alias=True, mode="json")})
    event_id = store.add_task_event("restart-replay", {
        "statusUpdate": {
            "taskId": "restart-replay", "contextId": "ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
        },
    })
    client = TestClient(TestServer(create_app(config, Store(tmp_path / "durable.sqlite3"))))
    await client.start_server()
    try:
        response = await client.post(
            "/tasks/restart-replay:subscribe",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert [frame["id"] for frame in _sse_frames(await response.text())] == [event_id]
    finally:
        await client.close()


async def test_subscribe_rejects_invalid_last_event_id(server_client):
    client, store = server_client
    message = Message(role="user", parts=[{"text": "wait"}])
    store.insert_task(
        Task(id="invalid-resume", status=TaskStatus(state=TaskState.SUBMITTED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    response = await client.post(
        "/tasks/invalid-resume:subscribe",
        headers={"Authorization": "Bearer test-secret-token", "Last-Event-ID": "not-a-number"},
    )
    assert response.status == 400
    assert (await response.json())["code"] == "invalid_last_event_id"


async def test_cancel_working_task_terminates_process_and_persists_event(server_client, config):
    client, store = server_client
    config["executor"]["command"] = [
        sys.executable, "-c", "import time; time.sleep(30)", "{prompt}",
    ]
    config["executor"]["cancel_grace_seconds"] = 0.1
    response = await client.post(
        "/message:stream",
        headers={"Authorization": "Bearer test-secret-token"},
        json={"message": {"role": "user", "parts": [{"text": "cancel me"}]}},
    )
    initial = _sse_frames((await response.content.readuntil(b"\n\n")).decode())[0]
    working = _sse_frames((await response.content.readuntil(b"\n\n")).decode())[0]
    task_id = initial["data"]["task"]["id"]
    assert working["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"

    manager = client.app[EXECUTOR_MANAGER_KEY]
    for _ in range(50):
        if await manager.has_process(task_id):
            break
        await asyncio.sleep(0.01)
    assert await manager.has_process(task_id)
    canceled = await client.post(
        f"/tasks/{task_id}:cancel", headers={"Authorization": "Bearer test-secret-token"},
    )
    assert (await canceled.json())["status"]["state"] == "TASK_STATE_CANCELED"
    assert not await manager.has_process(task_id)
    assert store.get_task_lease(task_id) is None
    final_frames = _sse_frames(await response.text())
    assert final_frames[-1]["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert store.list_task_events(task_id)[-1].event["statusUpdate"]["status"]["state"] == "TASK_STATE_CANCELED"


async def test_startup_recovers_stale_task_but_can_be_disabled(config, tmp_path):
    path = tmp_path / "startup-recovery.sqlite3"
    store = Store(path)
    message = Message(role="user", parts=[{"text": "stale"}])
    store.insert_task(
        Task(id="stale-startup", contextId="ctx", status=TaskStatus(state=TaskState.WORKING), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE id='stale-startup'")
    client = TestClient(TestServer(create_app(config, store)))
    await client.start_server()
    await client.close()
    assert store.get_task("stale-startup").status.state == TaskState.FAILED
    assert store.list_task_events("stale-startup")

    disabled_path = tmp_path / "disabled-recovery.sqlite3"
    disabled_store = Store(disabled_path)
    disabled_store.insert_task(
        Task(id="untouched", contextId="ctx", status=TaskStatus(state=TaskState.WORKING), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    with sqlite3.connect(disabled_path) as conn:
        conn.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE id='untouched'")
    config["recovery"]["recover_on_startup"] = False
    client = TestClient(TestServer(create_app(config, disabled_store)))
    await client.start_server()
    await client.close()
    assert disabled_store.get_task("untouched").status.state == TaskState.WORKING


async def test_subscribe_polling_reads_external_events_without_duplicates(config, tmp_path):
    config["streaming"]["poll_interval_seconds"] = 0.01
    path = tmp_path / "polling.sqlite3"
    store = Store(path)
    message = Message(role="user", parts=[{"text": "wait"}])
    store.insert_task(
        Task(id="polled", contextId="ctx", status=TaskStatus(state=TaskState.WORKING), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    initial_id = store.add_task_event("polled", {"task": {"id": "polled"}})
    client = TestClient(TestServer(create_app(config, store)))
    await client.start_server()
    try:
        response = await client.post(
            "/tasks/polled:subscribe",
            headers={"Authorization": "Bearer test-secret-token", "Last-Event-ID": str(initial_id)},
        )
        external = Store(path)
        working = {
            "statusUpdate": {
                "taskId": "polled", "contextId": "ctx",
                "status": {"state": "TASK_STATE_WORKING"},
            }
        }
        working_id = external.add_task_event("polled", working)
        client.app[EVENT_BROKER_KEY].publish(
            "polled", {"id": working_id, "event": "message", "data": working},
        )
        terminal = {
            "statusUpdate": {
                "taskId": "polled", "contextId": "ctx",
                "status": {"state": "TASK_STATE_COMPLETED"},
            }
        }
        terminal_id = external.add_task_event("polled", terminal)
        frames = _sse_frames(await asyncio.wait_for(response.text(), timeout=2))
        assert [frame["id"] for frame in frames] == [working_id, terminal_id]
    finally:
        await client.close()


async def test_subscribe_bounds_initial_replay(config, tmp_path):
    config["streaming"]["max_replay_events"] = 2
    store = Store(tmp_path / "bounded.sqlite3")
    message = Message(role="user", parts=[{"text": "done"}])
    store.insert_task(
        Task(id="bounded", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    ids = [store.add_task_event("bounded", {"message": {"role": "agent", "parts": [{"text": str(n)}]}}) for n in range(5)]
    client = TestClient(TestServer(create_app(config, store)))
    await client.start_server()
    try:
        response = await client.post(
            "/tasks/bounded:subscribe", headers={"Authorization": "Bearer test-secret-token"},
        )
        assert [frame["id"] for frame in _sse_frames(await response.text())] == ids[:2]
    finally:
        await client.close()


async def test_cancel_unowned_working_task_is_explicit_and_persisted(server_client):
    client, store = server_client
    message = Message(role="user", parts=[{"text": "remote work"}])
    store.insert_task(
        Task(id="unowned", contextId="ctx", status=TaskStatus(state=TaskState.WORKING), history=[message]),
        {"message": message.model_dump(by_alias=True, mode="json")},
    )
    response = await client.post(
        "/tasks/unowned:cancel", headers={"Authorization": "Bearer test-secret-token"},
    )
    task = await response.json()
    text = task["status"]["message"]["parts"][0]["text"]
    assert text == "Cancellation requested, but this server process does not own the executor process."
    assert task["metadata"]["cancellation"]["localProcessTerminated"] is False
    stored = store.list_task_events("unowned")[-1].event
    assert stored["statusUpdate"]["status"]["message"]["parts"][0]["text"] == text
    assert "test-secret-token" not in json.dumps(stored)
