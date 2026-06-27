import json
import re

from hermes_a2a_bridge.models import build_agent_card


def _card(config):
    return build_agent_card(config).model_dump(by_alias=True, exclude_none=True, mode="json")


def _file_references(card):
    return card.get("metadata", {}).get("hermesA2ABridge", {}).get("fileReferences")


def _assert_no_unsafe_file_advertising(data):
    serialized = json.dumps(data)
    lowered = serialized.lower()
    assert _file_references(data) is None
    assert "fileReferences" not in serialized
    assert "image/" not in lowered
    assert "audio/" not in lowered
    assert "video/" not in lowered
    assert "upload" not in lowered
    assert "inline bytes" not in lowered
    assert "remote url fetch" not in lowered
    assert "storage_path" not in lowered
    assert "storagepath" not in lowered
    assert "storage_dir" not in lowered
    assert "C:\\" not in serialized
    assert "C:/" not in serialized
    assert "/home/" not in serialized
    assert "/tmp/" not in serialized
    assert "\\\\" not in serialized
    assert "raw" not in lowered
    assert "base64" not in lowered


def test_default_agent_card_has_required_text_fields_and_no_token(config):
    data = _card(config)
    assert data["name"] == "Hermes Agent"
    assert data["url"] == "http://127.0.0.1:8765"
    assert data["capabilities"]["streaming"] is True
    assert data["capabilities"]["pushNotifications"] is False
    assert data["capabilities"]["extendedAgentCard"] is False
    assert "text/plain" in data["defaultInputModes"]
    assert "text/plain" in data["defaultOutputModes"]
    assert any(skill["id"] == "hermes-chat" for skill in data["skills"])
    assert data["preferredTransport"] == "HTTP+JSON"
    assert data["additionalInterfaces"] == [{
        "url": "http://127.0.0.1:8765",
        "transport": "HTTP+JSON",
    }]
    assert data["supportedInterfaces"] == [{
        "url": "http://127.0.0.1:8765",
        "protocolBinding": "HTTP+JSON",
        "protocolVersion": "1.0",
    }]
    assert "image/png" not in data["defaultInputModes"]
    assert "application/pdf" not in data["defaultInputModes"]
    assert "file" not in json.dumps(data).lower()
    assert config["server"]["auth_token"] not in json.dumps(data)


def test_default_agent_card_does_not_advertise_file_reference_support(config):
    data = _card(config)
    _assert_no_unsafe_file_advertising(data)
    assert "file" not in json.dumps(data).lower()


def test_half_open_file_parts_only_agent_card_does_not_advertise_support(config):
    config["parts"]["allow_file_parts"] = True
    config["parts"]["allow_file_id_references"] = False
    data = _card(config)
    _assert_no_unsafe_file_advertising(data)


def test_half_open_file_id_only_agent_card_does_not_advertise_support(config):
    config["parts"]["allow_file_parts"] = False
    config["parts"]["allow_file_id_references"] = True
    data = _card(config)
    _assert_no_unsafe_file_advertising(data)


def test_open_gate_agent_card_advertises_limited_stored_file_id_references_only(config):
    config["parts"]["allow_file_parts"] = True
    config["parts"]["allow_file_id_references"] = True
    data = _card(config)
    file_refs = _file_references(data)
    serialized = json.dumps(data)

    assert file_refs == {
        "supported": True,
        "scope": "pre_staged_local_file_id_references_only",
        "acceptedShapes": [{"file": {"fileId": "file_..."}}],
        "requiresAuth": True,
        "requiresConfig": [
            "parts.allow_file_parts",
            "parts.allow_file_id_references",
        ],
        "unsupported": [
            "inline_bytes",
            "uri_file_references",
            "remote_url_fetch",
            "arbitrary_local_paths",
            "uploads",
        ],
    }
    assert data["defaultInputModes"] == ["text/plain", "application/json"]
    assert data["defaultOutputModes"] == ["text/plain", "application/json"]
    assert "image/" not in serialized.lower()
    assert "audio/" not in serialized.lower()
    assert "video/" not in serialized.lower()
    assert re.search(r"\bfile_[A-Za-z0-9]{20,}\b", serialized.replace("file_...", "")) is None
    assert "storage_path" not in serialized.lower()
    assert "storagepath" not in serialized.lower()
    assert "storage_dir" not in serialized.lower()
    assert "C:\\" not in serialized
    assert "C:/" not in serialized
    assert "/home/" not in serialized
    assert "/tmp/" not in serialized
    assert "\\\\" not in serialized
    assert "raw" not in serialized.lower()
    assert "base64" not in serialized.lower()
    assert config["server"]["auth_token"] not in serialized
