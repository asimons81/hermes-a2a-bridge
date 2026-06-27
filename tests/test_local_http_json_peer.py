from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_a2a_bridge import client
from hermes_a2a_bridge.errors import ClientError
from hermes_a2a_bridge.models import AgentCard, Message, StreamResponse, Task

from local_http_json_peer import LocalHttpJsonPeer


BLACKBOX = Path(__file__).parent / "fixtures" / "blackbox"
FIXTURES = BLACKBOX / "local_http_json_peer"


def _load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _parse_sse(name: str) -> list[dict]:
    events = []
    text = (FIXTURES / name).read_text(encoding="utf-8").strip()
    for block in text.split("\n\n"):
        data = []
        event_id = None
        event_name = "message"
        for line in block.splitlines():
            if not line or line.startswith(":"):
                continue
            field, value = line.split(":", 1)
            value = value.lstrip(" ")
            if field == "id":
                event_id = value
            elif field == "event":
                event_name = value
            elif field == "data":
                data.append(value)
        if data:
            events.append(client._parse_sse_event(event_id, event_name, data))
    return events


@pytest.mark.asyncio
async def test_local_http_json_peer_runs_full_client_flow():
    peer = LocalHttpJsonPeer()
    base = await peer.start()
    try:
        card = await client.fetch_agent_card(base)
        assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"

        sent = await client.send_message(base, "hello local peer", metadata={"probe": "phase-10"})
        assert sent["id"] == "local-peer-task-1"

        streamed = [event async for event in client.stream_message(base, "stream local peer")]
        assert streamed[-1]["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"

        task = await client.get_task(base, sent["id"])
        assert task["metadata"]["source"] == "local-http-json-peer"

        with pytest.raises(ClientError) as structured:
            await client.send_message(base, "structured-error")
        assert structured.value.payload["error"]["details"][0]["metadata"]["bridgeCode"] == (
            "local_peer_structured_error"
        )

        assert peer.client is not None
        file_response = await peer.client.post(
            "/message:send",
            headers={"A2A-Version": "1.0", "Content-Type": "application/a2a+json"},
            json={
                "message": {
                    "messageId": "local-file-rejection",
                    "role": "ROLE_USER",
                    "parts": [{"file": {"name": "report.txt", "mimeType": "text/plain"}}],
                }
            },
        )
        file_body = await file_response.json()
        assert file_response.status == 400
        assert file_body["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"

        assert peer.captures["discover"][-1]["request"]["method"] == "GET"
        assert peer.captures["message_send"][-1]["request"]["path"] == "/message:send"
        assert peer.captures["message_stream"][-1]["response"]["body"].startswith("id: 1")
        assert peer.captures["task_lookup"][-1]["request"]["path"] == "/tasks/local-peer-task-1"
        assert peer.captures["structured_error"][-1]["response"]["body"]
        assert peer.captures["file_part_rejection"][-1]["response"]["body"]
        serialized = json.dumps(peer.captures).lower()
        assert "bearer " not in serialized
        assert "c:\\" not in serialized
        assert "c:/" not in serialized
        assert "/home/" not in serialized
        assert "/tmp/" not in serialized
    finally:
        await peer.close()


def test_local_http_json_peer_fixture_directory_exists_and_notes_state_peer_type():
    assert FIXTURES.is_dir()
    notes = (FIXTURES / "notes.md").read_text(encoding="utf-8")
    assert "Peer type: test-only local compatibility peer" in notes
    assert "not a public real-world peer" in notes


def test_local_http_json_peer_json_fixtures_parse():
    AgentCard.model_validate(_load_json("agent_card.json"))
    Message.model_validate(_load_json("message_send_request.json")["message"])
    Task.model_validate(_load_json("message_send_response.json")["task"])
    Message.model_validate(_load_json("message_stream_request.json")["message"])
    Task.model_validate(_load_json("task_lookup_response.json"))
    assert _load_json("structured_error_response.json")["error"]["status"] == "INVALID_ARGUMENT"
    assert _load_json("file_part_rejection_response.json")["error"]["details"][0]["metadata"]["bridgeCode"] == (
        "unsupported_part_type"
    )


def test_local_http_json_peer_stream_fixture_parses():
    events = _parse_sse("message_stream_events.sse")
    assert len(events) == 3
    for event in events:
        StreamResponse.model_validate(event["data"])
    assert events[-1]["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_local_http_json_peer_request_fixtures_parse():
    assert _load_json("discover_request.json")["method"] == "GET"
    assert _load_json("task_lookup_request.json")["path"] == "/tasks/local-peer-task-1"
    assert _load_json("structured_error_request.json")["body"]["message"]["parts"][0]["text"] == "structured-error"
    assert "file" in _load_json("file_part_rejection_request.json")["body"]["message"]["parts"][0]


def test_local_http_json_peer_fixtures_are_sanitized_and_do_not_claim_file_support():
    forbidden = (
        "bearer ",
        "auth_token",
        "access_token=",
        "storage_path",
        "storagePath",
        "C:\\",
        "C:/",
        "/home/",
        "/tmp/",
        "\\\\",
    )
    for path in FIXTURES.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            assert not any(marker.lower() in lowered for marker in forbidden), path
            assert "raw file bytes" not in lowered, path

    card = _load_json("agent_card.json")
    serialized_card = json.dumps(card).lower()
    assert not any("file" in mode.lower() for mode in card["defaultInputModes"])
    assert "image/" not in serialized_card
    assert "audio/" not in serialized_card
    assert "video/" not in serialized_card
    assert _load_json("file_part_rejection_response.json")["error"]["details"][0]["metadata"]["bridgeCode"] == (
        "unsupported_part_type"
    )
