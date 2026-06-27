from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_a2a_bridge.cli import register_cli
from hermes_a2a_bridge.config import default_config
from hermes_a2a_bridge.models import build_agent_card
from hermes_a2a_bridge.operations import add_remote_url_file_reference
from hermes_a2a_bridge.server import create_app
from hermes_a2a_bridge.store import Store


ROOT = Path(__file__).parents[1]


def _route_pairs(config, tmp_path):
    app = create_app(config, Store(tmp_path / "audit-routes.sqlite3"))
    return sorted((route.method, route.resource.canonical) for route in app.router.routes())


def test_file_boundary_route_list_has_only_expected_public_surface(config, tmp_path):
    routes = _route_pairs(config, tmp_path)
    paths = {path for _, path in routes}
    file_routes = sorted((method, path) for method, path in routes if path.startswith("/files"))

    assert file_routes == [
        ("GET", "/files/{file_id}"),
        ("GET", "/files/{file_id}/metadata"),
        ("HEAD", "/files/{file_id}"),
        ("HEAD", "/files/{file_id}/metadata"),
    ]
    assert not any(path.startswith("/v1") for path in paths)
    assert not any("upload" in path.lower() for path in paths)
    assert not any(method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/files") for method, path in routes)


def test_file_boundary_cli_has_file_lifecycle_commands_and_only_stored_id_send_flags():
    parser = argparse.ArgumentParser()
    register_cli(parser)
    help_text = parser.format_help()

    send_parser = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    send = send_parser.choices["send"]
    stream = send_parser.choices["stream"]
    files = send_parser.choices["files"]
    files_sub = next(action for action in files._actions if isinstance(action, argparse._SubParsersAction))

    assert "--file-id" in send.format_help()
    assert "--file-id" in stream.format_help()
    assert "--file " not in send.format_help()
    assert "--file " not in stream.format_help()
    for command in ("send", "stream"):
        with pytest.raises(SystemExit):
            parser.parse_args([command, "http://remote.test", "hello", "--file", "report.txt"])
    assert set(files_sub.choices) == {
        "add-url",
        "attach-artifact",
        "cleanup-orphans",
        "delete",
        "download",
        "fetch-metadata",
        "ingest",
        "list",
        "repair",
        "scan",
        "show",
        "stats",
        "verify",
    }
    assert "files" in help_text


def test_file_boundary_defaults_keep_runtime_gate_closed():
    config = default_config()
    assert config["parts"]["allow_file_parts"] is False
    assert config["parts"]["allow_file_id_references"] is False
    assert config["parts"]["allow_remote_url_file_references"] is False
    assert config["parts"]["allow_inline_file_bytes"] is False
    assert config["files"]["auto_fetch_remote_urls"] is False
    assert config["files"]["allow_inline_bytes"] is False
    assert config["server"]["require_auth"] is True
    assert config["server"]["host"] == "127.0.0.1"
    assert config["server"]["allow_remote_hosts"] is False


def test_file_boundary_agent_card_does_not_advertise_broad_file_support(config):
    card = build_agent_card(config).model_dump(by_alias=True, mode="json")
    serialized = json.dumps(card).lower()

    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["pushNotifications"] is False
    assert card["preferredTransport"] == "HTTP+JSON"
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card["defaultInputModes"] == ["text/plain", "application/json"]
    assert card["defaultOutputModes"] == ["text/plain", "application/json"]
    assert "file" not in serialized
    assert "image/" not in serialized
    assert "audio/" not in serialized
    assert "video/" not in serialized


async def test_file_boundary_inbound_sdk_style_file_parts_rejected_for_send_and_stream(config, tmp_path):
    token = config["server"]["auth_token"]
    client = TestClient(TestServer(create_app(config, Store(tmp_path / "audit-reject.sqlite3"))))
    await client.start_server()
    try:
        payload = {
            "message": {
                "messageId": "sdk-file-boundary",
                "role": "ROLE_USER",
                "parts": [{"raw": "aGVsbG8=", "filename": "report.txt", "mediaType": "text/plain"}],
            }
        }
        for route in ("/message:send", "/message:stream"):
            response = await client.post(
                route,
                headers={"Authorization": f"Bearer {token}", "A2A-Version": "1.0"},
                json=payload,
            )
            body = await response.json()
            assert response.status == 400
            assert body["error"]["details"][0]["metadata"]["bridgeCode"] == "unsupported_part_type"
            assert "File parts are not supported yet" in body["error"]["message"]
    finally:
        await client.close()


def test_metadata_only_remote_url_reference_does_not_fetch_network(config, tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("metadata-only remote URL reference attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", fail_if_called)
    result = add_remote_url_file_reference(
        "https://example.test/report.pdf?token=secret",
        Store(tmp_path / "remote-url.sqlite3"),
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
    )

    file = result["file"]
    assert file["metadataOnly"] is True
    assert file["bytesAvailable"] is False
    assert file["sourceUrl"] == "https://example.test/report.pdf"
    serialized = json.dumps(result)
    assert "token=secret" not in serialized
    assert "storage_path" not in serialized


def test_inbound_file_parts_design_exists_and_preserves_closed_runtime_boundary():
    design = ROOT / "docs" / "INBOUND_FILE_PARTS_DESIGN.md"
    boundary = ROOT / "docs" / "FILE_BOUNDARY_STATUS.md"
    assert design.exists()

    text = design.read_text(encoding="utf-8")
    assert "Stored file ID references are implemented" in text
    assert "Stored file ID references require both `parts.allow_file_parts: true` and `parts.allow_file_id_references: true`" in text
    assert "Inline bytes/base64 should remain rejected" in text
    assert "Do not pass local storage paths to the executor by default" in text
    assert "Do not automatically create task artifacts from inbound references" in text

    boundary_text = boundary.read_text(encoding="utf-8")
    assert "docs/INBOUND_FILE_PARTS_DESIGN.md" in boundary_text
    assert "stored file id inbound support" in boundary_text.lower()
