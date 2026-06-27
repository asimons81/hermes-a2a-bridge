from __future__ import annotations

import json
import re
from pathlib import Path

from hermes_a2a_bridge.client import _parse_sse_event
from hermes_a2a_bridge.models import AgentCard, Artifact, Message, StreamResponse, Task


BLACKBOX = Path(__file__).parent / "fixtures" / "blackbox"
ROOT = Path(__file__).parents[1]


def load_json(name: str):
    return json.loads((BLACKBOX / name).read_text(encoding="utf-8"))


def load_nested_json(*parts: str):
    return json.loads((BLACKBOX.joinpath(*parts)).read_text(encoding="utf-8"))


def parse_sse_fixture(name: str):
    events = []
    for block in (BLACKBOX / name).read_text(encoding="utf-8").strip().split("\n\n"):
        event_id = None
        event_name = "message"
        data = []
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
        events.append(_parse_sse_event(event_id, event_name, data))
    return events


def parse_nested_sse_fixture(*parts: str):
    path = BLACKBOX.joinpath(*parts)
    events = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        event_id = None
        event_name = "message"
        data = []
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
            events.append(_parse_sse_event(event_id, event_name, data))
    return events


def test_captured_sdk_agent_card_fixture_parses():
    card = AgentCard.model_validate(load_json("sdk_1_0_agent_card.json"))
    assert card.supported_interfaces[0].protocol_binding == "HTTP+JSON"
    assert card.capabilities.streaming is True


def test_captured_sdk_1_0_3_fixtures_parse():
    card = AgentCard.model_validate(load_nested_json("sdk_1_0_3", "agent_card.json"))
    assert card.version == "1.0.3-fixture"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    request = load_nested_json("sdk_1_0_3", "message_send_request.json")
    assert Message.model_validate(request["message"]).metadata == {"trace": "blackbox"}
    response = load_nested_json("sdk_1_0_3", "message_send_response.json")
    assert Task.model_validate(response["task"]).metadata["source"] == "a2a-sdk-1.0.3"
    assert Task.model_validate(
        load_nested_json("sdk_1_0_3", "task_lookup_response.json")
    ).status.state.value == "TASK_STATE_COMPLETED"
    for event in parse_nested_sse_fixture("sdk_1_0_3", "stream_events.sse"):
        StreamResponse.model_validate(event["data"])
    hermes_card = AgentCard.model_validate(
        load_nested_json("sdk_1_0_3", "sdk_client_sees_hermes_card.json")
    )
    assert hermes_card.version == "0.3.6"
    unsupported = load_nested_json("sdk_1_0_3", "unsupported_part_error.json")
    assert unsupported["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_message"


def test_captured_a2a_samples_fixture_documents_unsupported_transport():
    card = load_nested_json("a2a_samples", "agent_card.json")
    assert card["name"] == "Hello World Agent"
    assert card["protocolVersion"] == "0.3"
    assert card["preferredTransport"] == "JSONRPC"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    error = load_nested_json("a2a_samples", "error_response.json")
    assert error["code"] == "unsupported_protocol_version"
    assert error["protocol_version"] == "0.3"
    notes = (BLACKBOX / "a2a_samples" / "notes.md").read_text(encoding="utf-8")
    assert "Discovery result: passed" in notes
    assert "Operation result: skipped" in notes


def test_captured_sdk_send_response_and_task_lookup_fixtures_parse():
    response = load_json("sdk_1_0_message_send_response.json")
    assert Task.model_validate(response["task"]).id == "sdk-task-1"
    assert Task.model_validate(load_json("sdk_1_0_task_lookup_response.json")).status.state.value == "TASK_STATE_COMPLETED"


def test_captured_sdk_stream_fixture_parses():
    events = parse_sse_fixture("sdk_1_0_stream_events.sse")
    assert len(events) == 3
    for event in events:
        StreamResponse.model_validate(event["data"])
    assert events[-1]["data"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_hermes_server_fixture_seen_by_sdk_remains_stable():
    card = AgentCard.model_validate(load_json("hermes_server_agent_card_seen_by_sdk.json"))
    assert card.version == "0.3.6"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.capabilities.push_notifications is False


def test_sdk_client_unsupported_part_error_is_structured():
    payload = load_json("sdk_client_unsupported_part_error.json")
    error = payload["error"]
    assert error["status"] == "INVALID_ARGUMENT"
    assert error["details"][0]["reason"] == "CONTENT_TYPE_NOT_SUPPORTED"
    assert error["details"][0]["metadata"]["bridgeCode"] == "unsupported_message"


def test_captured_requests_and_compatibility_fixtures_are_useful():
    request = load_json("sdk_1_0_message_send_request.json")
    assert Message.model_validate(request["message"]).metadata == {"trace": "blackbox"}
    compat_card = AgentCard.model_validate(load_json("compatibility_0_3_agent_card.json"))
    compat_send = load_json("compatibility_0_3_message_send.json")
    assert compat_card.protocol_version == "0.3.0"
    assert compat_send["endpoint"] == "/v1/message:send"


def test_sdk_style_data_part_fixtures_parse():
    request = load_json("sdk_style_data_part_request.json")
    message = Message.model_validate(request["message"])
    assert message.parts[0].data == {"sdk": True, "version": "1.0"}
    assert "kind" not in request["message"]["parts"][0]
    response = load_json("sdk_style_data_artifact_response.json")
    task = Task.model_validate(response["task"])
    assert task.artifacts[0]["parts"][0]["data"]["ok"] is True
    assert "kind" not in task.artifacts[0]["parts"][0]


def test_captured_sdk_data_part_fixtures_parse():
    request = load_nested_json("data_parts", "sdk_1_1_0", "data_message_send_request.json")
    message = Message.model_validate(request["message"])
    assert message.parts[0].data["nested"]["ok"] is True
    assert "kind" not in request["message"]["parts"][0]

    mixed = load_nested_json("data_parts", "sdk_1_1_0", "mixed_text_data_send_request.json")
    mixed_message = Message.model_validate(mixed["message"])
    assert mixed_message.parts[1].data[1]["name"] == "Grace"
    assert "kind" not in mixed["message"]["parts"][1]

    for name in (
        "data_message_send_response.json",
        "mixed_text_data_send_response.json",
    ):
        task = Task.model_validate(load_nested_json("data_parts", "sdk_1_1_0", name)["task"])
        assert task.artifacts[0]["parts"][0]["data"]
        assert "kind" not in task.artifacts[0]["parts"][0]

    data_task = Task.model_validate(
        load_nested_json("data_parts", "sdk_1_1_0", "data_artifact_task_response.json")
    )
    assert data_task.artifacts[0]["parts"][0]["data"]["rows"] == [1, 2]
    for event in parse_nested_sse_fixture("data_parts", "sdk_1_1_0", "data_artifact_stream_events.sse"):
        StreamResponse.model_validate(event["data"])

    error = load_nested_json("data_parts", "sdk_1_1_0", "unsupported_data_error.json")
    assert error["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"
    notes = (BLACKBOX / "data_parts" / "sdk_1_1_0" / "notes.md").read_text(encoding="utf-8")
    assert "no `kind` or `type`" in notes


def test_captured_sdk_1_0_3_data_part_fixtures_parse():
    request = load_nested_json("data_parts", "sdk_1_0_3", "data_message_send_request.json")
    assert Message.model_validate(request["message"]).parts[0].data["nested"]["ok"] is True
    task = Task.model_validate(
        load_nested_json("data_parts", "sdk_1_0_3", "data_message_send_response.json")["task"]
    )
    assert task.metadata["requestDataPartCount"] == 1
    for event in parse_nested_sse_fixture("data_parts", "sdk_1_0_3", "data_artifact_stream_events.sse"):
        StreamResponse.model_validate(event["data"])


def test_captured_sdk_client_to_hermes_data_fixtures_parse():
    request = load_nested_json("data_parts", "sdk_client_to_hermes", "sdk_data_send_to_hermes_request.json")
    assert Message.model_validate(request["message"]).parts[0].data["sdkClient"] is True
    mixed = load_nested_json("data_parts", "sdk_client_to_hermes", "sdk_mixed_send_to_hermes_request.json")
    assert Message.model_validate(mixed["message"]).parts[1].data[1]["name"] == "Grace"

    for name in (
        "hermes_data_send_response_to_sdk.json",
        "hermes_mixed_send_response_to_sdk.json",
    ):
        task = Task.model_validate(load_nested_json("data_parts", "sdk_client_to_hermes", name)["task"])
        assert task.artifacts[0]["parts"][0]["data"]["ok"] is True
        assert "kind" not in task.artifacts[0]["parts"][0]

    for event in parse_nested_sse_fixture("data_parts", "sdk_client_to_hermes", "hermes_data_stream_to_sdk.sse"):
        StreamResponse.model_validate(event["data"])

    error = load_nested_json("data_parts", "sdk_client_to_hermes", "hermes_file_part_rejection_to_sdk.json")
    assert error["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"


def test_sdk_file_part_rejection_fixtures_parse_and_keep_card_honest():
    for sdk_dir in ("sdk_1_1_0", "sdk_1_0_3"):
        base = ("file_parts", sdk_dir)
        send_request = load_nested_json(*base, "sdk_file_send_to_hermes_request.json")
        assert "raw" in send_request["message"]["parts"][0]
        send_response = load_nested_json(*base, "hermes_file_rejection_response.json")
        assert send_response["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"
        stream_request = load_nested_json(*base, "sdk_file_stream_to_hermes_request.json")
        assert "url" in stream_request["message"]["parts"][0]
        stream_response = load_nested_json(*base, "hermes_file_stream_rejection_response.json")
        assert stream_response["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"
        card = AgentCard.model_validate(load_nested_json(*base, "hermes_agent_card_seen_by_sdk.json"))
        assert not any("file" in mode.lower() for mode in card.default_input_modes)
        assert not any("image" in mode.lower() or "audio" in mode.lower() or "video" in mode.lower() for mode in card.default_input_modes)
        assert card.capabilities.push_notifications is False


def test_metadata_only_file_reference_fixtures_parse_and_are_sanitized():
    hermes_owned = ("file_references", "hermes_owned")
    metadata = load_nested_json(*hermes_owned, "file_metadata_response.json")
    assert metadata["success"] is True
    assert metadata["file"]["fileId"].startswith("file_")
    assert "storage_path" not in json.dumps(metadata)
    task = Task.model_validate(load_nested_json(*hermes_owned, "file_artifact_task.json"))
    Artifact.model_validate(task.artifacts[0])
    event = load_nested_json(*hermes_owned, "file_artifact_update_event.json")
    StreamResponse.model_validate(event)
    for parsed in parse_nested_sse_fixture(*hermes_owned, "file_subscribe_replay_events.sse"):
        StreamResponse.model_validate(parsed["data"])
    headers = load_nested_json(*hermes_owned, "file_download_headers.json")
    assert headers["Content-Disposition"].startswith("attachment;")
    assert "Content-Length" in headers

    remote = ("file_references", "remote_url")
    remote_metadata = load_nested_json(*remote, "remote_url_metadata_response.json")
    assert remote_metadata["file"]["metadataOnly"] is True
    assert remote_metadata["file"]["bytesAvailable"] is False
    remote_task = Task.model_validate(load_nested_json(*remote, "remote_url_file_artifact_task.json"))
    remote_part = remote_task.artifacts[0]["parts"][0]["file"]
    assert remote_part["metadataOnly"] is True
    assert remote_part["bytesAvailable"] is False
    assert "uri" not in remote_part
    StreamResponse.model_validate(load_nested_json(*remote, "remote_url_artifact_update_event.json"))
    byte_error = load_nested_json(*remote, "remote_url_bytes_unavailable_error.json")
    assert byte_error["code"] == "file_bytes_unavailable"


def test_inbound_file_id_reference_fixtures_parse_and_are_sanitized():
    base = ("inbound_file_id_references",)
    Message.model_validate(load_nested_json(*base, "gates_closed_file_rejection_request.json")["message"])
    Message.model_validate(load_nested_json(*base, "gates_open_file_id_request.json")["message"])
    Task.model_validate(load_nested_json(*base, "gates_open_file_id_response.json")["task"])
    cli_send = Message.model_validate(load_nested_json(*base, "cli_send_file_id_request.json")["message"])
    cli_stream = Message.model_validate(load_nested_json(*base, "cli_stream_file_id_request.json")["message"])
    tool_send = Message.model_validate(load_nested_json(*base, "tool_send_file_id_request.json")["message"])
    assert cli_send.parts[1].file == {"fileId": "file_abcdefghijklmnopqrstuv"}
    assert cli_stream.parts[1].file == {"fileId": "file_abcdefghijklmnopqrstuv"}
    assert tool_send.parts[1].file == {"fileId": "file_abcdefghijklmnopqrstuv"}
    Task.model_validate(load_nested_json(*base, "cli_send_file_id_response.json")["task"])
    tool_response = load_nested_json(*base, "tool_send_file_id_response.json")
    assert tool_response["success"] is True
    Task.model_validate(tool_response["task"])
    for parsed in parse_nested_sse_fixture(*base, "cli_stream_file_id_events.sse"):
        StreamResponse.model_validate(parsed["data"])
    assert load_nested_json(*base, "cli_invalid_file_id_error.json")["code"] == "invalid_file_id"
    assert load_nested_json(*base, "tool_invalid_file_id_error.json")["code"] == "invalid_file_id"
    assert (
        load_nested_json(*base, "cli_closed_gate_error.json")["error"]["details"][0]["metadata"]["bridgeCode"]
        == "unsupported_part_type"
    )
    for name, code in (
        ("gates_closed_file_rejection_response.json", "unsupported_part_type"),
        ("unknown_file_id_error.json", "file_not_found"),
        ("remote_url_reference_error.json", "unsupported_remote_file_url"),
        ("inline_bytes_error.json", "unsupported_inline_file_bytes"),
        ("uri_reference_error.json", "unsupported_remote_file_url"),
        ("missing_bytes_error.json", "file_bytes_unavailable"),
        ("checksum_mismatch_error.json", "file_integrity_failed"),
        ("tool_closed_gate_error.json", "unsupported_part_type"),
        ("tool_unknown_file_id_error.json", "file_not_found"),
        ("tool_remote_url_row_error.json", "unsupported_remote_file_url"),
    ):
        payload = load_nested_json(*base, name)
        assert payload["error"]["details"][0]["metadata"]["bridgeCode"] == code

    forbidden = ("bearer ", "storage_path", "storagePath", "C:\\", "C:/", "/home/", "/tmp/", "\\\\")
    for path in (BLACKBOX / "inbound_file_id_references").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            assert not any(marker.lower() in lowered for marker in forbidden), path


def test_agent_card_file_reference_fixtures_parse_and_are_sanitized():
    base = ("agent_card_file_references",)
    default = AgentCard.model_validate(load_nested_json(*base, "default_agent_card_no_file_support.json"))
    half_parts = AgentCard.model_validate(load_nested_json(*base, "half_open_allow_file_parts_only_agent_card.json"))
    half_ids = AgentCard.model_validate(load_nested_json(*base, "half_open_allow_file_id_only_agent_card.json"))
    open_gate = AgentCard.model_validate(load_nested_json(*base, "open_gate_limited_file_reference_agent_card.json"))

    for card in (default, half_parts, half_ids):
        data = card.model_dump(by_alias=True, exclude_none=True, mode="json")
        serialized = json.dumps(data).lower()
        assert "file" not in serialized
        assert "image/" not in serialized
        assert "audio/" not in serialized
        assert "video/" not in serialized

    data = open_gate.model_dump(by_alias=True, exclude_none=True, mode="json")
    file_refs = data["metadata"]["hermesA2ABridge"]["fileReferences"]
    serialized = json.dumps(data)
    lowered = serialized.lower()
    assert file_refs["supported"] is True
    assert file_refs["scope"] == "pre_staged_local_file_id_references_only"
    assert file_refs["acceptedShapes"] == [{"file": {"fileId": "file_..."}}]
    assert file_refs["requiresAuth"] is True
    assert file_refs["requiresConfig"] == [
        "parts.allow_file_parts",
        "parts.allow_file_id_references",
    ]
    assert set(file_refs["unsupported"]) == {
        "inline_bytes",
        "uri_file_references",
        "remote_url_fetch",
        "arbitrary_local_paths",
        "uploads",
    }
    assert re.search(r"\bfile_[A-Za-z0-9]{20,}\b", serialized.replace("file_...", "")) is None
    assert "image/" not in lowered
    assert "audio/" not in lowered
    assert "video/" not in lowered

    forbidden = (
        "bearer ",
        "token=",
        "secret",
        "password",
        "user:pass",
        "storage_path",
        "storagePath",
        "storage_dir",
        "C:\\",
        "C:/",
        "/home/",
        "/tmp/",
        "\\\\",
        "base64",
        "raw bytes:",
    )
    for path in (BLACKBOX / "agent_card_file_references").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            assert not any(marker.lower() in lowered for marker in forbidden), path


def test_external_official_interop_fixtures_parse_and_are_sanitized():
    base = BLACKBOX / "external_official_interop"
    assert base.is_dir()

    notes = (base / "notes.md").read_text(encoding="utf-8")
    assert "could not emit that shape" in notes
    assert "No SDK-to-Hermes stored fileId request or response fixture exists" in notes
    assert "No public no-credential peer capture exists" in notes

    public_notes = (base / "public_peer_search_notes.md").read_text(encoding="utf-8")
    assert "No public no-auth HTTP+JSON A2A 1.0 endpoint was captured" in public_notes
    assert "public peer capture remains absent" in public_notes

    for name, version in (
        ("sdk_capability_probe_1_1_0.json", "1.1.0"),
        ("sdk_capability_probe_1_0_3.json", "1.0.3"),
    ):
        payload = json.loads((base / name).read_text(encoding="utf-8"))
        assert payload["sdkVersion"] == version
        assert payload["runtimeDependencyAdded"] is False
        assert "file" not in payload["partFields"]
        for shape in ("fileId", "uri", "bytes"):
            assert payload["results"][shape]["accepted"] is False
            assert "no field named file" in payload["results"][shape]["exception"]
        assert payload["results"]["sdk_url"]["accepted"] is True
        assert payload["results"]["sdk_raw"]["accepted"] is True

    card = AgentCard.model_validate(load_nested_json("external_official_interop", "sdk_to_hermes_agent_card.json"))
    card_data = card.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert card_data["metadata"]["hermesA2ABridge"]["fileReferences"]["scope"] == (
        "pre_staged_local_file_id_references_only"
    )

    for name in (
        "sdk_to_hermes_uri_rejection_response.json",
        "sdk_to_hermes_inline_bytes_rejection_response.json",
    ):
        payload = json.loads((base / name).read_text(encoding="utf-8"))
        assert payload["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"

    forbidden = (
        "bearer ",
        "authorization:",
        "access_token=",
        "api_key=",
        "token=",
        "password=",
        "signature=",
        "storage_path",
        "storagePath",
        "storage_dir",
        "C:\\",
        "C:/",
        "/home/",
        "/tmp/",
        "\\\\",
        "aGVsbG8=",
        "iVBORw0KGgo=",
        "full file-part conformance",
        "full A2A conformance",
    )
    for path in base.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            assert not any(marker.lower() in lowered for marker in forbidden), path

    assert not (base / "sdk_to_hermes_file_id_request.json").exists()
    assert not (base / "sdk_to_hermes_file_id_response.json").exists()


def test_file_reference_fixtures_do_not_expose_paths_credentials_or_tokens():
    credential_markers = ("bearer ", "token=", "secret", "password", "user:pass", "auth")
    local_path_markers = ("storage_path", "storagePath", "C:\\", "C:/", "/home/", "/tmp/", "\\\\")
    for path in (BLACKBOX / "file_references").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            assert not any(marker in lowered for marker in credential_markers), path
            assert not any(marker in text for marker in local_path_markers), path
    for path in (BLACKBOX / "file_parts").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8").lower()
            assert "bearer " not in text
            assert "integration-only-token" not in text


def test_interop_deviation_ledger_exists_and_mentions_bounded_scope():
    text = (ROOT / "docs" / "INTEROP.md").read_text(encoding="utf-8")
    assert "a2a-sdk 1.1.0" in text
    assert "a2a-sdk 1.0.3" in text
    assert "a2a-samples" in text
    assert "structured JSON data parts" in text
    assert "file-reference fixtures" in text
    assert "not full A2A conformance" in text


def test_all_blackbox_fixtures_do_not_contain_secrets_paths_bytes_or_overclaims():
    forbidden = [
        "bearer ",
        "authorization:",
        "blackbox-token",
        "integration-only-token",
        "auth_token",
        "access_token=",
        "api_key=",
        "token=",
        "password=",
        "signature=",
        "user:pass",
        "storage_path",
        "storagepath",
        "storage_dir",
        "c:\\",
        "c:/",
        "/home/",
        "/tmp/",
        "\\\\",
        ".sqlite",
        ".db",
        "file-bytes-secret",
        "raw staged file contents",
        "remote fetch supported",
        "inline bytes supported",
        "upload supported",
    ]
    overclaim_phrases = ("full file support", "full conformance")
    for path in BLACKBOX.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert not any(value in text for value in forbidden), path
        for phrase in overclaim_phrases:
            assert phrase not in text or "not " + phrase in text, path
        if "agvsbg8=" in text:
            assert "file_parts" in path.parts and "rejection" in path.name or "send_to_hermes_request" in path.name, path
