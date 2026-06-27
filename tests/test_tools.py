import json
import inspect
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hermes_a2a_bridge import tools
from hermes_a2a_bridge import schemas
from hermes_a2a_bridge.operations import add_remote_url_file_reference, ingest_local_file
from hermes_a2a_bridge.server import create_app
from hermes_a2a_bridge.store import Store


def test_tool_schema_names_are_exact():
    assert set(tools.HANDLERS) == {
        "a2a_discover_agent",
        "a2a_doctor_peer",
        "a2a_send_message",
        "a2a_get_task",
        "a2a_list_tasks",
        "a2a_cancel_task",
        "a2a_registry_add",
        "a2a_registry_list",
        "a2a_registry_remove",
    }


async def test_every_tool_returns_json_string():
    for handler in tools.HANDLERS.values():
        value = await handler({})
        assert isinstance(value, str)
        assert isinstance(json.loads(value), dict)


async def test_invalid_url_returns_json_error():
    result = json.loads(await tools.a2a_discover_agent({"url": "not-a-url"}))
    assert result["success"] is False
    assert "HTTP" in result["error"]


async def test_registry_name_resolves_and_token_is_not_echoed(config, monkeypatch):
    secret = "super-secret-registry-token"
    tools._store().registry_add("demo", "http://remote.test", secret)

    async def fake_card(url):
        assert url == "http://remote.test"
        return {"name": "Demo", "url": "http://preferred.test"}

    async def fake_send(base, text, token=None, context_id=None, timeout_seconds=None):
        assert base == "http://preferred.test"
        assert token == secret
        raise RuntimeError(f"request carrying {secret} failed")

    monkeypatch.setattr(tools.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(tools.client, "send_message", fake_send)
    raw = await tools.a2a_send_message({"agent_url": "demo", "message": "hello"})
    assert secret not in raw
    assert "[REDACTED]" in raw


async def test_registry_add_validates_name_and_returns_stable_shape():
    payload = json.loads(await tools.a2a_registry_add({"name": "bad name", "url": "http://demo.test"}))
    assert payload["success"] is False
    assert "Registry names" in payload["error"]


async def test_send_message_tool_supports_optional_data(config, monkeypatch):
    seen = {}

    async def fake_card(url):
        return {"name": "Demo", "url": "http://preferred.test"}

    async def fake_send(base, text=None, token=None, context_id=None, timeout_seconds=None, data=None):
        seen.update({"base": base, "text": text, "data": data})
        return {
            "id": "t1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"kind": "data", "data": data}]}],
        }

    monkeypatch.setattr(tools.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(tools.client, "send_message", fake_send)
    raw = await tools.a2a_send_message({"agent_url": "http://remote.test", "data": {"answer": 42}})
    payload = json.loads(raw)
    assert payload["success"] is True
    assert seen == {"base": "http://preferred.test", "text": None, "data": {"answer": 42}}
    assert payload["task"]["artifacts"][0]["parts"][0]["data"] == {"answer": 42}


def test_send_message_tool_schema_exposes_only_stored_file_ids():
    schema = schemas.TOOL_SCHEMAS["a2a_send_message"]["parameters"]
    properties = schema["properties"]

    assert "file_ids" in properties
    assert properties["file_ids"]["type"] == "array"
    assert properties["file_ids"]["items"]["pattern"] == "^file_[A-Za-z0-9_-]{16,}$"
    for forbidden in ("file_path", "file_paths", "path", "uri", "bytes"):
        assert forbidden not in properties


def test_send_message_tool_code_does_not_read_local_files_for_file_ids():
    source = inspect.getsource(tools)
    assert ".read_bytes(" not in source
    assert ".read_text(" not in source
    assert "open(" not in source


async def test_send_message_tool_accepts_optional_file_ids(config, monkeypatch):
    seen = {}

    async def fake_card(url):
        return {"name": "Demo", "url": "http://preferred.test"}

    async def fake_send(base, text=None, token=None, context_id=None, timeout_seconds=None, data=None, file_ids=None):
        seen.update({
            "base": base,
            "text": text,
            "data": data,
            "file_ids": file_ids,
        })
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

    monkeypatch.setattr(tools.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(tools.client, "send_message", fake_send)
    raw = await tools.a2a_send_message({
        "agent_url": "http://remote.test",
        "message": "analyze this",
        "file_ids": ["file_abcdefghijklmnopqrstuv"],
    })
    payload = json.loads(raw)
    assert payload["success"] is True
    assert seen["file_ids"] == ["file_abcdefghijklmnopqrstuv"]
    assert payload["task"]["metadata"]["inputFileReferences"][0]["fileId"] == "file_abcdefghijklmnopqrstuv"
    serialized = json.dumps(payload)
    assert "storage_path" not in serialized
    assert "hello" not in serialized


async def test_send_message_tool_empty_file_ids_behaves_like_omitted(config, monkeypatch):
    seen = {}

    async def fake_card(url):
        return {"name": "Demo", "url": "http://preferred.test"}

    async def fake_send(base, text=None, token=None, context_id=None, timeout_seconds=None, **kwargs):
        seen["kwargs"] = kwargs
        return {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"}}

    monkeypatch.setattr(tools.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(tools.client, "send_message", fake_send)
    payload = json.loads(await tools.a2a_send_message({
        "agent_url": "http://remote.test",
        "message": "hello",
        "file_ids": [],
    }))
    assert payload["success"] is True
    assert "file_ids" not in seen["kwargs"]


async def test_send_message_tool_multiple_file_ids_preserve_order(config, monkeypatch):
    seen = {}

    async def fake_card(url):
        return {"name": "Demo", "url": "http://preferred.test"}

    async def fake_send(base, text=None, token=None, context_id=None, timeout_seconds=None, file_ids=None):
        seen["file_ids"] = file_ids
        return {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"}}

    monkeypatch.setattr(tools.client, "fetch_agent_card", fake_card)
    monkeypatch.setattr(tools.client, "send_message", fake_send)
    payload = json.loads(await tools.a2a_send_message({
        "agent_url": "http://remote.test",
        "message": "compare",
        "file_ids": ["file_abcdefghijklmnopqrstuv", "file_bcdefghijklmnopqrstuvw"],
    }))
    assert payload["success"] is True
    assert seen["file_ids"] == ["file_abcdefghijklmnopqrstuv", "file_bcdefghijklmnopqrstuvw"]


@pytest.mark.parametrize("value", [
    "file_short",
    r"C:\Users\asimo\report.txt",
    "https://example.test/report.pdf",
])
async def test_send_message_tool_invalid_file_ids_reject_before_discovery(config, monkeypatch, value):
    async def fail_card(*args, **kwargs):
        raise AssertionError("invalid file_ids should fail before discovery")

    monkeypatch.setattr(tools.client, "fetch_agent_card", fail_card)
    payload = json.loads(await tools.a2a_send_message({
        "agent_url": "http://remote.test",
        "message": "hello",
        "file_ids": [value],
    }))
    assert payload["success"] is False
    assert payload["code"] == "invalid_file_id"


@pytest.mark.parametrize("value", ["file_abcdefghijklmnopqrstuv", {"id": "file_abcdefghijklmnopqrstuv"}])
async def test_send_message_tool_wrong_file_ids_type_rejects_before_discovery(config, monkeypatch, value):
    async def fail_card(*args, **kwargs):
        raise AssertionError("wrong file_ids type should fail before discovery")

    monkeypatch.setattr(tools.client, "fetch_agent_card", fail_card)
    payload = json.loads(await tools.a2a_send_message({
        "agent_url": "http://remote.test",
        "message": "hello",
        "file_ids": value,
    }))
    assert payload["success"] is False
    assert payload["code"] == "invalid_file_ids"


async def test_send_message_tool_generated_request_contains_only_file_id():
    seen = {}

    async def card(request):
        return web.json_response({"name": "Remote", "url": f"{request.scheme}://{request.host}"})

    async def send(request):
        seen["body"] = await request.json()
        return web.json_response({"task": {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"}}})

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", send)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        payload = json.loads(await tools.a2a_send_message({
            "agent_url": base,
            "message": "analyze this",
            "file_ids": ["file_abcdefghijklmnopqrstuv"],
        }))
    finally:
        await server.close()

    assert payload["success"] is True
    parts = seen["body"]["message"]["parts"]
    assert parts == [
        {"text": "analyze this", "mediaType": "text/plain"},
        {"file": {"fileId": "file_abcdefghijklmnopqrstuv"}},
    ]
    serialized = json.dumps(seen["body"])
    assert "path" not in serialized
    assert "uri" not in serialized
    assert "bytes" not in serialized
    assert "hello" not in serialized


def _stage_tool_file(tmp_path, config, store, content: bytes = b"hello") -> str:
    config["files"]["storage_dir"] = str(tmp_path / "controlled-storage")
    source = tmp_path / "report.txt"
    source.write_bytes(content)
    return ingest_local_file(source, store, config, name="report.txt")["file"]["fileId"]


async def _run_tool_against_server(config, store, args, monkeypatch):
    async def completed(prompt, config):
        return "ok"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    server = TestServer(create_app(config, store))
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        config["server"]["public_url"] = base
        return json.loads(await tools.a2a_send_message({"agent_url": base, **args}))
    finally:
        await server.close()


async def test_send_message_tool_closed_gate_rejection_is_structured(config, tmp_path, monkeypatch):
    store = Store(tmp_path / "closed.sqlite3")
    file_id = _stage_tool_file(tmp_path, config, store)
    payload = await _run_tool_against_server(
        config,
        store,
        {"message": "hello", "token": "test-secret-token", "file_ids": [file_id]},
        monkeypatch,
    )
    assert payload["success"] is False
    assert payload["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"
    assert "test-secret-token" not in json.dumps(payload)


async def test_send_message_tool_half_open_gate_rejection_is_structured(config, tmp_path, monkeypatch):
    config["parts"]["allow_file_parts"] = True
    store = Store(tmp_path / "half-open.sqlite3")
    file_id = _stage_tool_file(tmp_path, config, store)
    payload = await _run_tool_against_server(
        config,
        store,
        {"message": "hello", "token": "test-secret-token", "file_ids": [file_id]},
        monkeypatch,
    )
    assert payload["success"] is False
    assert payload["error"]["details"][0]["metadata"]["bridgeCode"] == "file_reference_disabled"


async def test_send_message_tool_open_gate_accepts_stored_local_file_id(config, tmp_path, monkeypatch):
    config["parts"]["allow_file_parts"] = True
    config["parts"]["allow_file_id_references"] = True
    store = Store(tmp_path / "open.sqlite3")
    file_id = _stage_tool_file(tmp_path, config, store, b"file-bytes-secret")
    payload = await _run_tool_against_server(
        config,
        store,
        {"message": "analyze this", "token": "test-secret-token", "file_ids": [file_id]},
        monkeypatch,
    )
    assert payload["success"] is True
    refs = payload["task"]["metadata"]["inputFileReferences"]
    assert refs[0]["fileId"] == file_id
    assert refs[0]["bytesAvailable"] is True
    serialized = json.dumps(payload)
    assert "storage_path" not in serialized
    assert str(tmp_path) not in serialized
    assert "file-bytes-secret" not in serialized
    assert "test-secret-token" not in serialized


async def test_send_message_tool_unknown_file_id_rejection_is_structured(config, tmp_path, monkeypatch):
    config["parts"]["allow_file_parts"] = True
    config["parts"]["allow_file_id_references"] = True
    payload = await _run_tool_against_server(
        config,
        Store(tmp_path / "unknown.sqlite3"),
        {"message": "hello", "token": "test-secret-token", "file_ids": ["file_unknownabcdefghijklmnop"]},
        monkeypatch,
    )
    assert payload["success"] is False
    assert payload["error"]["details"][0]["metadata"]["bridgeCode"] == "file_not_found"


async def test_send_message_tool_remote_url_row_rejection_is_structured(config, tmp_path, monkeypatch):
    config["parts"]["allow_file_parts"] = True
    config["parts"]["allow_file_id_references"] = True
    store = Store(tmp_path / "remote-url.sqlite3")
    file_id = add_remote_url_file_reference(
        "https://example.test/report.pdf?token=secret",
        store,
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
    )["file"]["fileId"]
    payload = await _run_tool_against_server(
        config,
        store,
        {"message": "hello", "token": "test-secret-token", "file_ids": [file_id]},
        monkeypatch,
    )
    assert payload["success"] is False
    assert payload["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_remote_file_url"
    serialized = json.dumps(payload)
    assert "token=secret" not in serialized
    assert "storage_path" not in serialized


async def test_send_message_tool_integrity_error_is_structured(config, tmp_path, monkeypatch):
    config["parts"]["allow_file_parts"] = True
    config["parts"]["allow_file_id_references"] = True
    store = Store(tmp_path / "integrity.sqlite3")
    file_id = _stage_tool_file(tmp_path, config, store, b"hello")
    row = store.get_file_attachment(file_id)
    Path(row["storage_path"]).write_bytes(b"changed")
    payload = await _run_tool_against_server(
        config,
        store,
        {"message": "hello", "token": "test-secret-token", "file_ids": [file_id]},
        monkeypatch,
    )
    assert payload["success"] is False
    assert payload["error"]["details"][0]["metadata"]["bridgeCode"] == "file_integrity_failed"
    serialized = json.dumps(payload)
    assert "test-secret-token" not in serialized
    assert "storage_path" not in serialized
