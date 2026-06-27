from __future__ import annotations

import argparse
import json

from aiohttp import web
from aiohttp.test_utils import TestServer

from hermes_a2a_bridge import cli, tools
from hermes_a2a_bridge.cli import a2a_command
from hermes_a2a_bridge.diagnostics import analyze_agent_card, diagnose_peer


def _card(**overrides):
    value = {
        "name": "Example Agent",
        "description": "Test peer",
        "version": "1.0.0",
        "url": "http://remote.test",
        "protocolVersion": "1.0",
        "preferredTransport": "HTTP+JSON",
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [],
        "supportedInterfaces": [{
            "url": "http://remote.test",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }],
    }
    value.update(overrides)
    return value


def test_analyze_compatible_http_json_peer():
    result = analyze_agent_card("http://remote.test", "http://remote.test/.well-known/agent-card.json", _card())

    assert result["ok"] is True
    assert result["status"] == "compatible"
    assert result["name"] == "Example Agent"
    assert result["protocol"] == {"binding": "HTTP+JSON", "version": "1.0"}
    assert result["capabilities"] == {
        "message_send": True,
        "message_stream": True,
        "tasks_get": True,
        "tasks_cancel": True,
        "tasks_subscribe": True,
        "file_references": False,
    }
    assert result["errors"] == []


def test_analyze_non_streaming_send_capable_peer_is_partial():
    result = analyze_agent_card(
        "http://remote.test",
        "http://remote.test/.well-known/agent-card.json",
        _card(capabilities={"streaming": False}),
    )

    assert result["status"] == "partially_compatible"
    assert result["capabilities"]["message_send"] is True
    assert result["capabilities"]["message_stream"] is False
    assert "streaming" in result["warnings"][0]


def test_analyze_json_rpc_only_peer_is_unsupported():
    result = analyze_agent_card(
        "http://remote.test",
        "http://remote.test/.well-known/agent-card.json",
        _card(
            preferredTransport="JSONRPC",
            supportedInterfaces=[{
                "url": "http://remote.test/rpc",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }],
        ),
    )

    assert result["status"] == "unsupported"
    assert result["protocol"]["binding"] == "JSONRPC"
    assert "JSON-RPC" in result["errors"][0]


def test_analyze_a2a_0_3_only_peer_is_unsupported():
    result = analyze_agent_card(
        "http://remote.test",
        "http://remote.test/.well-known/agent-card.json",
        _card(
            protocolVersion="0.3",
            supportedInterfaces=[{
                "url": "http://remote.test/v1",
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "0.3",
            }],
        ),
    )

    assert result["status"] == "unsupported"
    assert result["protocol"]["version"] == "0.3"
    assert "A2A 0.3" in result["errors"][0]


def test_analyze_file_reference_metadata():
    unsupported = analyze_agent_card(
        "http://remote.test",
        "http://remote.test/.well-known/agent-card.json",
        _card(metadata={"fileReferences": {"supported": True, "acceptedShapes": [{"file": {"uri": "https://..."}}]}}),
    )
    assert unsupported["capabilities"]["file_references"] is False
    assert "file-reference" in unsupported["warnings"][0]

    compatible = analyze_agent_card(
        "http://remote.test",
        "http://remote.test/.well-known/agent-card.json",
        _card(metadata={
            "hermesA2ABridge": {
                "fileReferences": {
                    "supported": True,
                    "scope": "pre_staged_local_file_id_references_only",
                    "acceptedShapes": [{"file": {"fileId": "file_..."}}],
                }
            }
        }),
    )
    assert compatible["capabilities"]["file_references"] is True


async def test_diagnose_invalid_missing_and_auth_agent_cards():
    async def invalid(request):
        return web.Response(text="{not-json", content_type="application/json")

    async def auth(request):
        return web.json_response({"error": "Bearer super-secret-token required"}, status=401)

    app = web.Application()
    app.router.add_get("/invalid/.well-known/agent-card.json", invalid)
    app.router.add_get("/auth/.well-known/agent-card.json", auth)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        invalid_result = await diagnose_peer(f"{base}/invalid")
        missing_result = await diagnose_peer(f"{base}/missing")
        auth_result = await diagnose_peer(f"{base}/auth", token="super-secret-token")
    finally:
        await server.close()

    assert invalid_result["status"] == "unknown"
    assert "valid JSON" in invalid_result["errors"][0]
    assert missing_result["status"] == "unknown"
    assert "not found" in missing_result["errors"][0]
    assert auth_result["status"] == "unknown"
    assert "authentication" in auth_result["errors"][0]
    assert "super-secret-token" not in json.dumps(auth_result)


async def test_diagnose_fetches_only_agent_card():
    seen = []

    async def card(request):
        seen.append((request.method, request.path))
        return web.json_response(_card(url=f"{request.scheme}://{request.host}"))

    async def forbidden_probe(request):
        raise AssertionError("doctor must not probe runtime routes")

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_route("*", "/message:send", forbidden_probe)
    app.router.add_route("*", "/files/{tail:.*}", forbidden_probe)
    server = TestServer(app)
    await server.start_server()
    try:
        result = await diagnose_peer(str(server.make_url("")).rstrip("/"))
    finally:
        await server.close()

    assert result["status"] == "compatible"
    assert seen == [("GET", "/.well-known/agent-card.json")]


def test_cli_doctor_json_and_human_output(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_doctor(url, token=None, timeout_seconds=None):
        assert url == "http://remote.test"
        assert token == "test-secret-token"
        assert timeout_seconds == 10
        return {
            "ok": True,
            "status": "compatible",
            "url": url,
            "agent_card_url": f"{url}/.well-known/agent-card.json",
            "name": "Remote",
            "protocol": {"binding": "HTTP+JSON", "version": "1.0"},
            "capabilities": {
                "message_send": True,
                "message_stream": True,
                "tasks_get": True,
                "tasks_cancel": True,
                "tasks_subscribe": True,
                "file_references": False,
            },
            "warnings": [],
            "errors": [],
            "recommendations": ["Send a small text request next."],
        }

    monkeypatch.setattr(cli, "diagnose_peer", fake_doctor)

    json_args = argparse.Namespace(
        a2a_command="doctor",
        agent="http://remote.test",
        token="test-secret-token",
        timeout=10,
        json=True,
    )
    assert a2a_command(json_args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "compatible"
    assert "test-secret-token" not in json.dumps(payload)

    json_args.json = False
    assert a2a_command(json_args) == 0
    output = capsys.readouterr().out
    assert "Peer Doctor: compatible" in output
    assert "message_send" in output
    assert "test-secret-token" not in output


async def test_tool_doctor_peer_returns_structured_diagnostic(config, monkeypatch):
    async def fake_doctor(url, token=None, timeout_seconds=None):
        assert url == "http://remote.test"
        assert token == "tool-token"
        assert timeout_seconds == 5
        return {
            "ok": True,
            "status": "partially_compatible",
            "url": url,
            "agent_card_url": f"{url}/.well-known/agent-card.json",
            "name": "Remote",
            "protocol": {"binding": "HTTP+JSON", "version": "1.0"},
            "capabilities": dict.fromkeys((
                "message_send",
                "message_stream",
                "tasks_get",
                "tasks_cancel",
                "tasks_subscribe",
                "file_references",
            ), False),
            "warnings": ["Bearer tool-token was not echoed."],
            "errors": [],
            "recommendations": [],
        }

    monkeypatch.setattr(tools, "diagnose_peer", fake_doctor)
    raw = await tools.a2a_doctor_peer({
        "agent_url": "http://remote.test",
        "token": "tool-token",
        "timeout_seconds": 5,
    })
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["status"] == "partially_compatible"
    assert "tool-token" not in raw
