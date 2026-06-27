from __future__ import annotations

import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from hermes_a2a_bridge import cli, files
from hermes_a2a_bridge.operations import add_remote_url_file_reference, ingest_local_file
from hermes_a2a_bridge.server import create_app
from hermes_a2a_bridge.store import Store


def _open_file_gates(config):
    config["parts"]["allow_file_parts"] = True
    config["parts"]["allow_file_id_references"] = True


def _payload(file_id: str):
    return {
        "message": {
            "messageId": "inbound-file-id",
            "role": "ROLE_USER",
            "parts": [
                {"text": "Summarize this file", "mediaType": "text/plain"},
                {"file": {"fileId": file_id}},
            ],
        }
    }


def _frames(body: str) -> list[dict]:
    frames = []
    for block in body.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            name, value = line.split(":", 1)
            fields[name] = value.lstrip()
        frames.append({"id": int(fields["id"]), "event": fields["event"], "data": json.loads(fields["data"])})
    return frames


def _stage_local_file(tmp_path, config, store, content: bytes = b"stored file bytes") -> str:
    config["files"]["storage_dir"] = str(tmp_path / "controlled-storage")
    source = tmp_path / "report.txt"
    source.write_bytes(content)
    return ingest_local_file(
        source,
        store,
        config,
        name="../../report final.txt",
        metadata={"purpose": "inbound-test"},
    )["file"]["fileId"]


async def _client(config, tmp_path):
    store = Store(tmp_path / "inbound.sqlite3")
    client = TestClient(TestServer(create_app(config, store)))
    await client.start_server()
    return client, store


async def test_gates_closed_reject_stored_file_id_send_and_stream(config, tmp_path):
    client, store = await _client(config, tmp_path)
    file_id = _stage_local_file(tmp_path, config, store)
    try:
        for route in ("/message:send", "/message:stream"):
            response = await client.post(
                route,
                headers={"Authorization": "Bearer test-secret-token", "A2A-Version": "1.0"},
                json=_payload(file_id),
            )
            body = await response.json()
            assert response.status == 400
            assert body["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"
    finally:
        await client.close()


async def test_file_parts_gate_without_file_id_gate_rejects(config, tmp_path):
    config["parts"]["allow_file_parts"] = True
    client, store = await _client(config, tmp_path)
    file_id = _stage_local_file(tmp_path, config, store)
    try:
        response = await client.post(
            "/message:send",
            headers={"Authorization": "Bearer test-secret-token", "A2A-Version": "1.0"},
            json=_payload(file_id),
        )
        body = await response.json()
        assert response.status == 400
        assert body["error"]["details"][0]["metadata"]["bridgeCode"] == "file_reference_disabled"
    finally:
        await client.close()


async def test_open_gates_accept_file_id_send_prompt_and_task_metadata(config, tmp_path, monkeypatch):
    _open_file_gates(config)
    seen = {}

    async def completed(prompt, config):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    client, store = await _client(config, tmp_path)
    file_id = _stage_local_file(tmp_path, config, store)
    try:
        response = await client.post(
            "/message:send",
            headers={"Authorization": "Bearer test-secret-token", "A2A-Version": "1.0"},
            json=_payload(file_id),
        )
        body = await response.json()
        assert response.status == 200
        task = body["task"]
        reference = task["metadata"]["inputFileReferences"][0]
        assert reference == {
            "fileId": file_id,
            "name": "report_final.txt",
            "mimeType": "text/plain",
            "sizeBytes": 17,
            "sha256": files.sha256_file(Path(store.get_file_attachment(file_id)["storage_path"])),
            "bytesAvailable": True,
            "source": "local",
        }
        assert task["history"][0]["parts"][1]["file"]["fileId"] == file_id
        assert task["metadata"]["executor"]["status"] == "completed"
        assert "File reference 1:" in seen["prompt"]
        assert f"- fileId: {file_id}" in seen["prompt"]
        assert "- name: report_final.txt" in seen["prompt"]
        serialized = json.dumps(task) + seen["prompt"]
        assert "storage_path" not in serialized
        assert str(tmp_path) not in serialized
        assert "stored file bytes" not in serialized
        assert "test-secret-token" not in serialized
        assert task["artifacts"][0]["parts"][0]["text"] == "ok"
        assert "file" not in json.dumps(task["artifacts"]).lower()
    finally:
        await client.close()


async def test_open_gates_accept_file_id_stream_and_subscribe_replay(config, tmp_path, monkeypatch):
    _open_file_gates(config)

    async def completed(prompt, config):
        return "streamed"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    client, store = await _client(config, tmp_path)
    file_id = _stage_local_file(tmp_path, config, store)
    try:
        response = await client.post(
            "/message:stream",
            headers={"Authorization": "Bearer test-secret-token"},
            json=_payload(file_id),
        )
        events = [frame["data"] for frame in _frames(await response.text())]
        task_id = events[0]["task"]["id"]
        assert events[0]["task"]["metadata"]["inputFileReferences"][0]["fileId"] == file_id
        stored = store.list_task_events(task_id)
        assert stored[0].event["task"]["metadata"]["inputFileReferences"][0]["fileId"] == file_id

        replay = await client.post(
            f"/tasks/{task_id}:subscribe",
            headers={"Authorization": "Bearer test-secret-token", "Last-Event-ID": "0"},
        )
        replay_events = [frame["data"] for frame in _frames(await replay.text())]
        assert replay_events[0]["task"]["metadata"]["inputFileReferences"][0]["fileId"] == file_id
        serialized = json.dumps(replay_events)
        assert "storage_path" not in serialized
        assert str(tmp_path) not in serialized
        assert "stored file bytes" not in serialized
    finally:
        await client.close()


async def test_open_gates_file_reference_errors_are_structured_and_safe(config, tmp_path):
    _open_file_gates(config)
    client, store = await _client(config, tmp_path)
    valid_id = _stage_local_file(tmp_path, config, store)
    row = store.get_file_attachment(valid_id)
    root = files.resolve_storage_root(config)
    try:
        missing_id = "file_missingabcdefghijklmnop"
        store.add_file_attachment(
            file_id=missing_id,
            filename="missing.txt",
            safe_filename="missing.txt",
            mime_type="text/plain",
            size_bytes=5,
            sha256="a" * 64,
            storage_path=str(root / "missing" / "content"),
            source="local_cli",
        )
        checksum_id = "file_checksumabcdefghijklmn"
        checksum_path = Path(row["storage_path"])
        store.add_file_attachment(
            file_id=checksum_id,
            filename="checksum.txt",
            safe_filename="checksum.txt",
            mime_type="text/plain",
            size_bytes=checksum_path.stat().st_size,
            sha256="b" * 64,
            storage_path=str(checksum_path),
            source="local_cli",
        )
        size_id = "file_sizemismatchabcdefghij"
        store.add_file_attachment(
            file_id=size_id,
            filename="size.txt",
            safe_filename="size.txt",
            mime_type="text/plain",
            size_bytes=999,
            sha256=files.sha256_file(checksum_path),
            storage_path=str(checksum_path),
            source="local_cli",
        )
        remote_id = add_remote_url_file_reference(
            "https://example.test/report.pdf?token=secret",
            store,
            config,
            name="report.pdf",
            declared_mime_type="application/pdf",
        )["file"]["fileId"]
        cases = [
            ({"file": {"fileId": "file_unknownabcdefghijklmnop"}}, "file_not_found"),
            ({"file": {"fileId": "not-a-file-id"}}, "invalid_file_reference"),
            ({"file": {"fileId": remote_id}}, "unsupported_remote_file_url"),
            ({"file": {"bytes": "aGVsbG8="}}, "unsupported_inline_file_bytes"),
            ({"file": {"uri": "https://example.test/report.pdf"}}, "unsupported_remote_file_url"),
            ({"file": {"path": r"C:\\Users\\asimo\\secret.txt"}}, "invalid_file_reference"),
            ({"file": {"fileId": missing_id}}, "file_bytes_unavailable"),
            ({"file": {"fileId": checksum_id}}, "file_integrity_failed"),
            ({"file": {"fileId": size_id}}, "file_integrity_failed"),
        ]
        for part, code in cases:
            payload = {"message": {"role": "ROLE_USER", "parts": [part]}}
            response = await client.post(
                "/message:send",
                headers={"Authorization": "Bearer test-secret-token", "A2A-Version": "1.0"},
                json=payload,
            )
            body = await response.json()
            serialized = json.dumps(body)
            assert response.status == 400
            assert body["error"]["details"][0]["metadata"]["bridgeCode"] == code
            assert "test-secret-token" not in serialized
            assert "token=secret" not in serialized
            assert "storage_path" not in serialized
            assert str(tmp_path) not in serialized
    finally:
        await client.close()


def test_cli_human_output_summarizes_input_file_references_safely(capsys):
    cli._render_text({
        "task": {
            "id": "t1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {
                "inputFileReferences": [{
                    "fileId": "file_abcdefghijklmnopqrstuv",
                    "name": "report.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": 5,
                    "sha256": "a" * 64,
                    "bytesAvailable": True,
                    "source": "local",
                }]
            },
            "artifacts": [],
        }
    })
    output = capsys.readouterr().out
    assert "[file: report.txt, text/plain, 5 bytes, id file_abcdefghijklmnopqrstuv]" in output
    assert "storage_path" not in output
