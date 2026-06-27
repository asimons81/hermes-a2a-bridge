from __future__ import annotations

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from hermes_a2a_bridge import cli, client, tools
from hermes_a2a_bridge.config import save_config
from hermes_a2a_bridge.errors import ClientError
from hermes_a2a_bridge.models import Message, StreamResponse, Task
from hermes_a2a_bridge.operations import add_remote_url_file_reference

from stored_file_id_e2e_harness import StoredFileIdE2EHarness, assert_safe_serialized


FIXTURES = Path(__file__).parent / "fixtures" / "blackbox" / "stored_file_id_e2e"


async def _start_open(config, tmp_path, monkeypatch):
    seen: list[str] = []

    async def completed(prompt, config):
        seen.append(prompt)
        return "stored-id e2e ok"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    harness = StoredFileIdE2EHarness(config, tmp_path)
    base = await harness.start(open_gates=True)
    return harness, base, seen


def _bridge_code(payload: dict) -> str:
    return payload["error"]["details"][0]["metadata"]["bridgeCode"]


async def test_client_send_stream_task_and_subscribe_open_gate_e2e(config, tmp_path, monkeypatch):
    harness, base, seen = await _start_open(config, tmp_path, monkeypatch)
    try:
        first = harness.stage_file("first.txt", b"stored-file-e2e-secret")
        second = harness.stage_file("second.txt", b"second-secret")
        task = await client.send_message(
            base,
            "compare staged files",
            token="test-secret-token",
            file_ids=[first["fileId"], second["fileId"]],
        )
        refs = task["metadata"]["inputFileReferences"]
        assert [item["fileId"] for item in refs] == [first["fileId"], second["fileId"]]
        assert [item["name"] for item in refs] == ["first.txt", "second.txt"]
        assert all(item["bytesAvailable"] is True for item in refs)
        assert "File reference 1:" in seen[-1]
        assert first["fileId"] in seen[-1]

        events = [
            event
            async for event in client.stream_message(
                base,
                "stream staged file",
                token="test-secret-token",
                file_ids=[first["fileId"]],
            )
        ]
        streamed_task = events[0]["data"]["task"]
        assert streamed_task["metadata"]["inputFileReferences"][0]["fileId"] == first["fileId"]
        task_id = streamed_task["id"]

        looked_up = await client.get_task(base, task_id, "test-secret-token")
        assert looked_up["metadata"]["inputFileReferences"][0]["fileId"] == first["fileId"]

        replay = [
            event
            async for event in client.subscribe_task(base, task_id, "test-secret-token")
        ]
        assert replay[0]["data"]["task"]["metadata"]["inputFileReferences"][0]["fileId"] == first["fileId"]

        serialized = {"task": task, "events": events, "lookup": looked_up, "replay": replay, "captures": harness.captures}
        assert_safe_serialized(serialized, tmp_path=tmp_path)
        assert "storage_path" not in json.dumps(refs)
    finally:
        await harness.close()


async def test_client_open_gate_rejects_non_stored_id_shapes(config, tmp_path, monkeypatch):
    harness, base, _ = await _start_open(config, tmp_path, monkeypatch)
    try:
        remote_file = add_remote_url_file_reference(
            "https://example.test/report.pdf?token=secret",
            harness.store,
            config,
            name="report.pdf",
            declared_mime_type="application/pdf",
        )["file"]
        cases = [
            ({"file": {"fileId": remote_file["fileId"]}}, "unsupported_remote_file_url"),
            ({"file": {"bytes": "aGVsbG8="}}, "unsupported_inline_file_bytes"),
            ({"file": {"uri": "https://example.test/report.pdf"}}, "unsupported_remote_file_url"),
            ({"file": {"path": r"C:\\Users\\asimo\\secret.txt"}}, "invalid_file_reference"),
            ({"file": {"name": "missing-bytes.txt"}}, "unsupported_file_reference"),
        ]
        for part, code in cases:
            payload = {"message": {"role": "ROLE_USER", "parts": [part]}}
            async with client.aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base}/message:send",
                    json=payload,
                    headers={
                        "Authorization": "Bearer test-secret-token",
                        "A2A-Version": "1.0",
                        "Content-Type": "application/a2a+json",
                    },
                ) as response:
                    body = await response.json()
            assert response.status == 400
            assert _bridge_code(body) == code
            assert_safe_serialized(body, tmp_path=tmp_path)
            assert "token=secret" not in json.dumps(body)
    finally:
        await harness.close()


async def test_client_closed_and_half_open_gates_reject_file_ids(config, tmp_path, monkeypatch):
    async def completed(prompt, config):
        return "should not run"

    monkeypatch.setattr("hermes_a2a_bridge.server.execute", completed)
    closed = StoredFileIdE2EHarness(deepcopy(config), tmp_path / "closed")
    closed_base = await closed.start(open_gates=False)
    try:
        file_id = closed.stage_file()["fileId"]
        with pytest.raises(ClientError) as caught:
            await client.send_message(closed_base, "closed", "test-secret-token", file_ids=[file_id])
        assert _bridge_code(caught.value.payload) == "unsupported_part_type"
    finally:
        await closed.close()

    half_config = deepcopy(config)
    half_config["parts"]["allow_file_parts"] = True
    half = StoredFileIdE2EHarness(half_config, tmp_path / "half")
    half_base = await half.start(open_gates=False)
    try:
        file_id = half.stage_file()["fileId"]
        with pytest.raises(ClientError) as caught:
            await client.send_message(half_base, "half", "test-secret-token", file_ids=[file_id])
        assert _bridge_code(caught.value.payload) == "file_reference_disabled"
    finally:
        await half.close()


async def test_cli_send_and_stream_open_gate_e2e(config, tmp_path, monkeypatch, capsys):
    harness, base, _ = await _start_open(config, tmp_path, monkeypatch)
    try:
        file_id = harness.stage_file()["fileId"]
        monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "cli-home"))
        save_config(config)

        send_args = argparse.Namespace(
            a2a_command="send",
            agent=base,
            message="cli send stored id",
            file_id=[file_id],
            token="test-secret-token",
            json=True,
        )
        assert await asyncio.to_thread(cli.a2a_command, send_args) == 0
        send_payload = json.loads(capsys.readouterr().out)
        assert send_payload["task"]["metadata"]["inputFileReferences"][0]["fileId"] == file_id
        assert_safe_serialized(send_payload, tmp_path=tmp_path)

        human_args = argparse.Namespace(
            a2a_command="send",
            agent=base,
            message="cli human stored id",
            file_id=[file_id],
            token="test-secret-token",
            json=False,
        )
        assert await asyncio.to_thread(cli.a2a_command, human_args) == 0
        human = capsys.readouterr().out
        assert f"id {file_id}" in human
        assert_safe_serialized(human, tmp_path=tmp_path)

        stream_args = argparse.Namespace(
            a2a_command="stream",
            agent=base,
            message="cli stream stored id",
            file_id=[file_id],
            token="test-secret-token",
            json=True,
        )
        assert await asyncio.to_thread(cli.a2a_command, stream_args) == 0
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert lines[0]["data"]["task"]["metadata"]["inputFileReferences"][0]["fileId"] == file_id
        assert_safe_serialized(lines, tmp_path=tmp_path)
    finally:
        await harness.close()


async def test_cli_closed_gate_rejection_is_clean_e2e(config, tmp_path, monkeypatch, capsys):
    closed = StoredFileIdE2EHarness(config, tmp_path)
    base = await closed.start(open_gates=False)
    try:
        file_id = closed.stage_file()["fileId"]
        monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "cli-closed-home"))
        save_config(config)
        args = argparse.Namespace(
            a2a_command="send",
            agent=base,
            message="closed",
            file_id=[file_id],
            token="test-secret-token",
            json=True,
        )
        assert await asyncio.to_thread(cli.a2a_command, args) == 1
        payload = json.loads(capsys.readouterr().out)
        assert _bridge_code(payload) == "unsupported_part_type"
        assert_safe_serialized(payload, tmp_path=tmp_path)
    finally:
        await closed.close()


async def test_tool_send_open_gate_and_rejections_e2e(config, tmp_path, monkeypatch):
    harness, base, _ = await _start_open(config, tmp_path, monkeypatch)
    try:
        file_id = harness.stage_file()["fileId"]
        payload = json.loads(await tools.a2a_send_message({
            "agent_url": base,
            "message": "tool stored id",
            "token": "test-secret-token",
            "file_ids": [file_id],
        }))
        assert payload["success"] is True
        assert payload["task"]["metadata"]["inputFileReferences"][0]["fileId"] == file_id
        assert_safe_serialized(payload, tmp_path=tmp_path)

        remote_file = add_remote_url_file_reference(
            "https://example.test/report.pdf?token=secret",
            harness.store,
            config,
            name="report.pdf",
            declared_mime_type="application/pdf",
        )["file"]["fileId"]
        rejected = json.loads(await tools.a2a_send_message({
            "agent_url": base,
            "message": "tool remote row",
            "token": "test-secret-token",
            "file_ids": [remote_file],
        }))
        assert rejected["success"] is False
        assert _bridge_code(rejected) == "unsupported_remote_file_url"
        assert_safe_serialized(rejected, tmp_path=tmp_path)
    finally:
        await harness.close()


def _load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _parse_sse(name: str) -> list[dict]:
    text = (FIXTURES / name).read_text(encoding="utf-8").strip()
    events = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, value = line.split(":", 1)
            fields[key] = value.lstrip()
        events.append(client._parse_sse_event(fields.get("id"), fields.get("event", "message"), [fields["data"]]))
    return events


def test_stored_file_id_e2e_fixture_directory_and_notes():
    assert FIXTURES.is_dir()
    notes = (FIXTURES / "notes.md").read_text(encoding="utf-8")
    assert "local open-gate stored-ID evidence" in notes
    assert "not public peer conformance" in notes


def test_stored_file_id_e2e_json_and_sse_fixtures_parse():
    for path in FIXTURES.glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    Message.model_validate(_load_json("client_send_file_id_request.json")["body"]["message"])
    Task.model_validate(_load_json("client_send_file_id_response.json")["task"])
    Message.model_validate(_load_json("client_stream_file_id_request.json")["body"]["message"])
    Task.model_validate(_load_json("tool_send_file_id_response.json")["task"])
    for event in _parse_sse("client_stream_file_id_events.sse"):
        StreamResponse.model_validate(event["data"])


def test_stored_file_id_e2e_fixtures_are_safe_and_scoped():
    forbidden = (
        "bearer ",
        "test-secret-token",
        "auth_token",
        "access_token=",
        "storage_path",
        "storagepath",
        "C:\\",
        "C:/",
        "/home/",
        "/tmp/",
        "\\\\",
        "stored-file-e2e-secret",
        "token=secret",
        "user:pass",
        "full file support",
        "broad file support",
        "full conformance",
    )
    for path in FIXTURES.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            assert not any(marker.lower() in lowered for marker in forbidden), path
    for name in (
        "client_send_file_id_response.json",
        "client_stream_file_id_events.sse",
        "cli_send_file_id_response.json",
        "tool_send_file_id_response.json",
    ):
        assert "inputFileReferences" in (FIXTURES / name).read_text(encoding="utf-8")
    expected_errors = {
        "closed_gate_rejection_response.json": "unsupported_part_type",
        "remote_url_row_rejection_response.json": "unsupported_remote_file_url",
        "inline_bytes_rejection_response.json": "unsupported_inline_file_bytes",
    }
    for name, code in expected_errors.items():
        assert _bridge_code(_load_json(name)) == code
