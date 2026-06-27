from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_a2a_bridge import client
from hermes_a2a_bridge.config import default_config
from hermes_a2a_bridge.models import build_agent_card

from raw_capture_harness import RawCaptureHarness, sanitize_path_qs


ROOT = Path(__file__).parents[1]
BLACKBOX = Path(__file__).parent / "fixtures" / "blackbox"


def _task(task_id: str = "capture-task") -> dict:
    return {
        "task": {
            "id": task_id,
            "contextId": "capture-context",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "history": [],
            "artifacts": [{
                "artifactId": "capture-artifact",
                "parts": [{"text": "captured"}],
            }],
            "metadata": {"source": "raw-capture-harness"},
        }
    }


@pytest.mark.asyncio
async def test_raw_capture_harness_records_and_redacts_json_requests():
    harness = RawCaptureHarness(json_response=_task())
    base = await harness.start()
    try:
        task = await client.send_message(
            base,
            "capture C:\\Users\\asimo\\secret.txt",
            token="capture-secret-token",
            metadata={"localPath": "C:/Users/asimo/secret.txt"},
        )
        assert task["id"] == "capture-task"
        captured = harness.captures[-1]["request"]
        assert captured["method"] == "POST"
        assert captured["path"] == "/message:send"
        assert captured["headers"]["Authorization"] == "[REDACTED]"
        assert captured["body"]["message"]["parts"][0]["text"] == "capture [LOCAL_PATH]"
        assert captured["body"]["message"]["metadata"]["localPath"] == "[LOCAL_PATH]"
        serialized = json.dumps(harness.captures)
        assert "capture-secret-token" not in serialized
        assert "C:\\Users\\asimo" not in serialized
        assert "C:/Users/asimo" not in serialized
    finally:
        await harness.close()


def test_raw_capture_harness_redacts_token_like_query_params():
    assert sanitize_path_qs("/message:send?access_token=abc&safe=ok&signature=xyz") == (
        "/message:send?access_token=%5BREDACTED%5D&safe=ok&signature=%5BREDACTED%5D"
    )


@pytest.mark.asyncio
async def test_raw_capture_harness_records_sse_frames():
    events = [
        {"task": _task("stream-task")["task"]},
        {
            "statusUpdate": {
                "taskId": "stream-task",
                "contextId": "capture-context",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "metadata": {"final": True},
            }
        },
    ]
    harness = RawCaptureHarness(json_response=_task("stream-task"), sse_events=events)
    base = await harness.start()
    try:
        streamed = [event async for event in client.stream_message(base, "stream me")]
        assert streamed[-1]["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        response_body = harness.captures[-1]["response"]["body"]
        assert "event: message" in response_body
        assert "stream-task" in response_body
    finally:
        await harness.close()


def test_external_interop_doc_and_no_real_peer_notes_exist():
    text = (ROOT / "docs" / "EXTERNAL_INTEROP.md").read_text(encoding="utf-8")
    assert "Phase 9" in text
    assert "No public no-auth HTTP+JSON 1.0 peer was confirmed runnable" in text
    assert "Raw Capture Harness" in text
    notes = (BLACKBOX / "external_real_peer" / "notes.md").read_text(encoding="utf-8")
    assert "Real-peer run result: skipped" in notes
    assert "No sanitized real-peer exchange fixtures are present" in notes


def test_external_real_peer_fixtures_have_no_tokens_or_local_paths():
    forbidden = ("bearer ", "auth_token", "access_token=", "C:\\", "C:/", "/home/", "/tmp/")
    for path in (BLACKBOX / "external_real_peer").rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert not any(marker.lower() in text for marker in forbidden), path


def test_phase_9_agent_card_still_does_not_advertise_file_support():
    card = build_agent_card(default_config()).model_dump(by_alias=True, mode="json")
    serialized = json.dumps(card).lower()
    assert "file" not in serialized
    assert "image/" not in serialized
    assert "audio/" not in serialized
    assert "video/" not in serialized
    assert card["defaultInputModes"] == ["text/plain", "application/json"]
