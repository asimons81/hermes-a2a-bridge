from __future__ import annotations

import argparse
import json

from aiohttp import web
from aiohttp.test_utils import TestServer

from hermes_a2a_bridge import cli, tools
from hermes_a2a_bridge.cli import a2a_command
from hermes_a2a_bridge.diagnostics import (
    DEFAULT_LIVE_PROBE_MESSAGE,
    DEFAULT_STREAM_PROBE_MESSAGE,
    analyze_agent_card,
    diagnose_peer,
)


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
    assert result["live_probe"] == {"enabled": False, "attempted": False}
    assert seen == [("GET", "/.well-known/agent-card.json")]


async def test_live_probe_sends_one_diagnostic_message_and_gets_task():
    seen = []

    async def card(request):
        base = f"{request.scheme}://{request.host}"
        seen.append((request.method, request.path))
        return web.json_response(_card(url=base, supportedInterfaces=[{
            "url": base,
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }]))

    async def send(request):
        body = await request.json()
        seen.append((request.method, request.path, body))
        return web.json_response({
            "task": {
                "id": "task-live-probe",
                "status": {"state": "TASK_STATE_COMPLETED"},
            }
        })

    async def get_task(request):
        seen.append((request.method, request.path))
        return web.json_response({
            "id": request.match_info["task_id"],
            "status": {"state": "TASK_STATE_COMPLETED"},
        })

    async def forbidden(request):
        raise AssertionError(f"unexpected live probe route: {request.method} {request.path}")

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", send)
    app.router.add_get("/tasks/{task_id}", get_task)
    app.router.add_post("/message:stream", forbidden)
    app.router.add_post("/tasks/{task_id}:cancel", forbidden)
    app.router.add_post("/tasks/{task_id}:subscribe", forbidden)
    app.router.add_route("*", "/files/{tail:.*}", forbidden)
    server = TestServer(app)
    await server.start_server()
    try:
        result = await diagnose_peer(str(server.make_url("")).rstrip("/"), live_probe=True)
    finally:
        await server.close()

    assert result["status"] == "compatible"
    probe = result["live_probe"]
    assert probe["enabled"] is True
    assert probe["attempted"] is True
    assert probe["message_send"] is True
    assert probe["task_id"] == "task-live-probe"
    assert probe["task_get"] is True
    assert probe["status"] == "passed"
    assert [item[:2] for item in seen] == [
        ("GET", "/.well-known/agent-card.json"),
        ("POST", "/message:send"),
        ("GET", "/tasks/task-live-probe"),
    ]
    parts = seen[1][2]["message"]["parts"]
    assert parts == [{"text": DEFAULT_LIVE_PROBE_MESSAGE, "mediaType": "text/plain"}]
    assert "file" not in json.dumps(seen[1][2])


async def test_live_probe_uses_custom_message_and_redacts_token():
    seen = {}

    async def card(request):
        base = f"{request.scheme}://{request.host}"
        assert request.headers["Authorization"] == "Bearer live-secret-token"
        return web.json_response(_card(url=base, supportedInterfaces=[{
            "url": base,
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }]))

    async def send(request):
        seen["authorization"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        return web.json_response({
            "task": {
                "id": "task-token",
                "status": {
                    "state": "TASK_STATE_COMPLETED",
                    "message": {"parts": [{"text": "live-secret-token"}]},
                },
            }
        })

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", send)
    server = TestServer(app)
    await server.start_server()
    try:
        result = await diagnose_peer(
            str(server.make_url("")).rstrip("/"),
            token="live-secret-token",
            live_probe=True,
            probe_message="custom diagnostic",
        )
    finally:
        await server.close()

    assert seen["authorization"] == "Bearer live-secret-token"
    assert seen["body"]["message"]["parts"][0]["text"] == "custom diagnostic"
    serialized = json.dumps(result)
    assert "live-secret-token" not in serialized


async def test_live_probe_skips_unsupported_invalid_and_missing_cards():
    calls = []

    async def unsupported_card(request):
        calls.append((request.method, request.path))
        return web.json_response(_card(
            preferredTransport="JSONRPC",
            supportedInterfaces=[{
                "url": f"{request.scheme}://{request.host}/rpc",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }],
        ))

    async def invalid(request):
        calls.append((request.method, request.path))
        return web.Response(text="{not-json", content_type="application/json")

    async def forbidden(request):
        raise AssertionError("live probe should not touch runtime routes")

    app = web.Application()
    app.router.add_get("/unsupported/.well-known/agent-card.json", unsupported_card)
    app.router.add_get("/invalid/.well-known/agent-card.json", invalid)
    app.router.add_route("*", "/{tail:.*}", forbidden)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        unsupported = await diagnose_peer(f"{base}/unsupported", live_probe=True)
        invalid_result = await diagnose_peer(f"{base}/invalid", live_probe=True)
        missing = await diagnose_peer(f"{base}/missing", live_probe=True)
    finally:
        await server.close()

    assert unsupported["live_probe"]["reason"] == "metadata_unsupported"
    assert invalid_result["live_probe"]["reason"] == "invalid_agent_card"
    assert missing["live_probe"]["reason"] == "agent_card_unavailable"
    assert all(item[0] == "GET" and item[1].endswith("/.well-known/agent-card.json") for item in calls)


async def test_live_probe_send_and_task_lookup_failures_are_structured():
    async def run(send_status: int, send_payload: dict, *, task_status: int = 200, task_payload: dict | None = None):
        async def card(request):
            base = f"{request.scheme}://{request.host}"
            return web.json_response(_card(url=base, supportedInterfaces=[{
                "url": base,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }]))

        async def send(request):
            return web.json_response(send_payload, status=send_status)

        async def get_task(request):
            return web.json_response(task_payload or {"error": "lookup failed"}, status=task_status)

        app = web.Application()
        app.router.add_get("/.well-known/agent-card.json", card)
        app.router.add_post("/message:send", send)
        app.router.add_get("/tasks/{task_id}", get_task)
        server = TestServer(app)
        await server.start_server()
        try:
            return await diagnose_peer(str(server.make_url("")).rstrip("/"), live_probe=True)
        finally:
            await server.close()

    failed_send = await run(500, {"error": "send failed"})
    assert failed_send["live_probe"]["status"] == "failed"
    assert failed_send["live_probe"]["message_send"] is False
    assert "send failed" in json.dumps(failed_send["live_probe"]["errors"])

    failed_lookup = await run(
        200,
        {"task": {"id": "task-get-fail", "status": {"state": "TASK_STATE_SUBMITTED"}}},
        task_status=503,
        task_payload={"error": "lookup failed"},
    )
    assert failed_lookup["live_probe"]["message_send"] is True
    assert failed_lookup["live_probe"]["task_id"] == "task-get-fail"
    assert failed_lookup["live_probe"]["task_get"] is False
    assert failed_lookup["live_probe"]["status"] == "passed_with_warnings"
    assert "lookup failed" in json.dumps(failed_lookup["live_probe"]["warnings"])


async def test_stream_probe_requires_live_probe_and_does_not_stream():
    seen = []

    async def card(request):
        base = f"{request.scheme}://{request.host}"
        seen.append((request.method, request.path))
        return web.json_response(_card(url=base, supportedInterfaces=[{
            "url": base,
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }]))

    async def forbidden(request):
        raise AssertionError(f"unexpected stream probe route: {request.method} {request.path}")

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", forbidden)
    app.router.add_post("/message:stream", forbidden)
    app.router.add_post("/tasks/{task_id}:cancel", forbidden)
    app.router.add_post("/tasks/{task_id}:subscribe", forbidden)
    app.router.add_route("*", "/files/{tail:.*}", forbidden)
    server = TestServer(app)
    await server.start_server()
    try:
        result = await diagnose_peer(str(server.make_url("")).rstrip("/"), stream_probe=True)
    finally:
        await server.close()

    assert result["live_probe"] == {"enabled": False, "attempted": False}
    assert result["stream_probe"] == {
        "enabled": True,
        "attempted": False,
        "status": "skipped",
        "reason": "live_probe_required",
    }
    assert seen == [("GET", "/.well-known/agent-card.json")]


async def test_live_probe_alone_does_not_stream():
    seen = []

    async def card(request):
        base = f"{request.scheme}://{request.host}"
        seen.append((request.method, request.path))
        return web.json_response(_card(url=base, supportedInterfaces=[{
            "url": base,
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }]))

    async def send(request):
        seen.append((request.method, request.path))
        return web.json_response({"task": {"id": "task-send-only", "status": {"state": "TASK_STATE_COMPLETED"}}})

    async def get_task(request):
        seen.append((request.method, request.path))
        return web.json_response({"id": request.match_info["task_id"], "status": {"state": "TASK_STATE_COMPLETED"}})

    async def forbidden(request):
        raise AssertionError(f"unexpected stream probe route: {request.method} {request.path}")

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", send)
    app.router.add_get("/tasks/{task_id}", get_task)
    app.router.add_post("/message:stream", forbidden)
    server = TestServer(app)
    await server.start_server()
    try:
        result = await diagnose_peer(str(server.make_url("")).rstrip("/"), live_probe=True)
    finally:
        await server.close()

    assert result["live_probe"]["status"] == "passed"
    assert result["stream_probe"] == {"enabled": False, "attempted": False}
    assert [item[:2] for item in seen] == [
        ("GET", "/.well-known/agent-card.json"),
        ("POST", "/message:send"),
        ("GET", "/tasks/task-send-only"),
    ]


async def test_stream_probe_sends_one_text_request_and_records_bounded_events():
    seen = []

    async def card(request):
        base = f"{request.scheme}://{request.host}"
        seen.append((request.method, request.path))
        return web.json_response(_card(url=base, supportedInterfaces=[{
            "url": base,
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }]))

    async def send(request):
        body = await request.json()
        seen.append((request.method, request.path, body))
        return web.json_response({"task": {"id": "task-live", "status": {"state": "TASK_STATE_COMPLETED"}}})

    async def stream(request):
        body = await request.json()
        seen.append((request.method, request.path, body))
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        events = [
            {"task": {"id": "task-stream", "status": {"state": "TASK_STATE_SUBMITTED"}}},
            {"artifactUpdate": {"taskId": "task-stream", "artifact": {"parts": [{"text": "hidden"}]}}},
            {"statusUpdate": {"taskId": "task-stream", "status": {"state": "TASK_STATE_COMPLETED"}}},
            {"statusUpdate": {"taskId": "task-stream", "status": {"state": "TASK_STATE_FAILED"}}},
        ]
        for index, event in enumerate(events, start=1):
            await response.write(f"id: {index}\ndata: {json.dumps(event)}\n\n".encode())
        await response.write_eof()
        return response

    async def forbidden(request):
        raise AssertionError(f"unexpected stream probe route: {request.method} {request.path}")

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", send)
    app.router.add_post("/message:stream", stream)
    app.router.add_post("/tasks/{task_id}:cancel", forbidden)
    app.router.add_post("/tasks/{task_id}:subscribe", forbidden)
    app.router.add_route("*", "/files/{tail:.*}", forbidden)
    server = TestServer(app)
    await server.start_server()
    try:
        result = await diagnose_peer(
            str(server.make_url("")).rstrip("/"),
            live_probe=True,
            stream_probe=True,
            stream_probe_max_events=20,
        )
    finally:
        await server.close()

    probe = result["stream_probe"]
    assert probe["enabled"] is True
    assert probe["attempted"] is True
    assert probe["status"] == "passed"
    assert probe["message_stream"] is True
    assert probe["events_received"] == 3
    assert probe["event_types"] == ["task", "artifactUpdate", "statusUpdate"]
    assert probe["task_id"] == "task-stream"
    assert probe["terminal_observed"] is True
    assert probe["warnings"] == []
    assert probe["errors"] == []
    stream_parts = seen[2][2]["message"]["parts"]
    assert stream_parts == [{"text": DEFAULT_STREAM_PROBE_MESSAGE, "mediaType": "text/plain"}]
    assert "file" not in json.dumps(seen[2][2])
    assert [item[:2] for item in seen] == [
        ("GET", "/.well-known/agent-card.json"),
        ("POST", "/message:send"),
        ("POST", "/message:stream"),
    ]


async def test_stream_probe_max_events_and_failure_paths_are_structured():
    async def run_stream(stream_handler, *, streaming=True):
        async def card(request):
            base = f"{request.scheme}://{request.host}"
            return web.json_response(_card(
                url=base,
                capabilities={"streaming": streaming},
                supportedInterfaces=[{
                    "url": base,
                    "protocolBinding": "HTTP+JSON",
                    "protocolVersion": "1.0",
                }],
            ))

        async def send(request):
            return web.json_response({"task": {"id": "task-live", "status": {"state": "TASK_STATE_COMPLETED"}}})

        app = web.Application()
        app.router.add_get("/.well-known/agent-card.json", card)
        app.router.add_post("/message:send", send)
        app.router.add_post("/message:stream", stream_handler)
        server = TestServer(app)
        await server.start_server()
        try:
            return await diagnose_peer(
                str(server.make_url("")).rstrip("/"),
                token="stream-secret-token",
                live_probe=True,
                stream_probe=True,
                stream_probe_max_events=2,
            )
        finally:
            await server.close()

    async def many_events(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for index in range(3):
            await response.write(
                f"id: {index}\ndata: {json.dumps({'task': {'id': 'task-many', 'status': {'state': 'TASK_STATE_WORKING'}}})}\n\n".encode()
            )
        await response.write_eof()
        return response

    async def malformed(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b"data: {not-json\n\n")
        await response.write_eof()
        return response

    async def failed(request):
        return web.json_response({"error": "stream-secret-token rejected"}, status=500)

    bounded = await run_stream(many_events)
    assert bounded["stream_probe"]["status"] == "passed_with_warnings"
    assert bounded["stream_probe"]["events_received"] == 2
    assert bounded["stream_probe"]["terminal_observed"] is False
    assert "max events" in bounded["stream_probe"]["warnings"][0]

    bad_sse = await run_stream(malformed)
    assert bad_sse["stream_probe"]["status"] == "failed"
    assert "malformed" in json.dumps(bad_sse["stream_probe"]["errors"])
    assert "stream-secret-token" not in json.dumps(bad_sse)

    failed_stream = await run_stream(failed)
    assert failed_stream["stream_probe"]["status"] == "failed"
    assert failed_stream["stream_probe"]["errors"][0]["http_status"] == 500
    assert "stream-secret-token" not in json.dumps(failed_stream)

    unsupported = await run_stream(failed, streaming=False)
    assert unsupported["stream_probe"]["attempted"] is False
    assert unsupported["stream_probe"]["reason"] == "metadata_streaming_unsupported"


def test_cli_doctor_json_and_human_output(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_doctor(
        url, token=None, timeout_seconds=None, live_probe=False, stream_probe=False,
        stream_probe_timeout=None, stream_probe_max_events=None, probe_message=None,
    ):
        assert url == "http://remote.test"
        assert token == "test-secret-token"
        assert timeout_seconds == 10
        assert live_probe is False
        assert stream_probe is False
        assert stream_probe_timeout is None
        assert stream_probe_max_events is None
        assert probe_message is None
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
            "live_probe": {"enabled": False, "attempted": False},
            "stream_probe": {"enabled": False, "attempted": False},
        }

    monkeypatch.setattr(cli, "diagnose_peer", fake_doctor)

    json_args = argparse.Namespace(
        a2a_command="doctor",
        agent="http://remote.test",
        token="test-secret-token",
        timeout=10,
        live_probe=False,
        live_probe_message=None,
        stream_probe=False,
        stream_probe_timeout=None,
        stream_probe_max_events=None,
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
    assert "Mode: metadata-only" in output
    assert "Live probe: disabled" in output
    assert "Stream probe: disabled" in output
    assert "message_send" in output
    assert "test-secret-token" not in output


def test_cli_live_probe_json_and_human_output(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_doctor(
        url, token=None, timeout_seconds=None, live_probe=False, stream_probe=False,
        stream_probe_timeout=None, stream_probe_max_events=None, probe_message=None,
    ):
        assert live_probe is True
        assert stream_probe is False
        assert stream_probe_timeout is None
        assert stream_probe_max_events is None
        assert probe_message == "custom ping"
        return {
            "ok": True,
            "status": "compatible",
            "url": url,
            "agent_card_url": f"{url}/.well-known/agent-card.json",
            "name": "Remote",
            "protocol": {"binding": "HTTP+JSON", "version": "1.0"},
            "capabilities": {
                "message_send": True,
                "message_stream": False,
                "tasks_get": True,
                "tasks_cancel": True,
                "tasks_subscribe": False,
                "file_references": False,
            },
            "warnings": [],
            "errors": [],
            "recommendations": [],
            "live_probe": {
                "enabled": True,
                "attempted": True,
                "message_send": True,
                "task_id": "task-cli",
                "task_get": True,
                "status": "passed",
                "warnings": [],
                "errors": [],
            },
            "stream_probe": {"enabled": False, "attempted": False},
        }

    monkeypatch.setattr(cli, "diagnose_peer", fake_doctor)
    args = argparse.Namespace(
        a2a_command="doctor",
        agent="http://remote.test",
        token=None,
        timeout=None,
        live_probe=True,
        live_probe_message="custom ping",
        stream_probe=False,
        stream_probe_timeout=None,
        stream_probe_max_events=None,
        json=True,
    )
    assert a2a_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["live_probe"]["enabled"] is True
    assert payload["live_probe"]["attempted"] is True
    assert payload["live_probe"]["status"] == "passed"

    args.json = False
    assert a2a_command(args) == 0
    output = capsys.readouterr().out
    assert "Mode: live-probed" in output
    assert "Message send: passed" in output
    assert "Task lookup: passed" in output
    assert "does not send files, fetch files, cancel tasks, or stream" in output
    assert "Stream probe: disabled" in output


def test_cli_stream_probe_json_and_human_output(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_A2A_HOME", str(tmp_path / "a2a"))
    from hermes_a2a_bridge.config import save_config

    save_config(config)

    async def fake_doctor(
        url, token=None, timeout_seconds=None, live_probe=False, stream_probe=False,
        stream_probe_timeout=None, stream_probe_max_events=None, probe_message=None,
    ):
        assert live_probe is True
        assert stream_probe is True
        assert stream_probe_timeout == 7
        assert stream_probe_max_events == 4
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
            "recommendations": [],
            "live_probe": {
                "enabled": True,
                "attempted": True,
                "message_send": True,
                "task_get": None,
                "status": "passed",
                "warnings": [],
                "errors": [],
            },
            "stream_probe": {
                "enabled": True,
                "attempted": True,
                "status": "passed",
                "message_stream": True,
                "events_received": 2,
                "event_types": ["task", "statusUpdate"],
                "task_id": "task-stream-cli",
                "terminal_observed": True,
                "warnings": [],
                "errors": [],
            },
        }

    monkeypatch.setattr(cli, "diagnose_peer", fake_doctor)
    args = argparse.Namespace(
        a2a_command="doctor",
        agent="http://remote.test",
        token=None,
        timeout=None,
        live_probe=True,
        live_probe_message=None,
        stream_probe=True,
        stream_probe_timeout=7,
        stream_probe_max_events=4,
        json=True,
    )
    assert a2a_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stream_probe"]["status"] == "passed"

    args.json = False
    assert a2a_command(args) == 0
    output = capsys.readouterr().out
    assert "Mode: stream-probed" in output
    assert "Stream probe: enabled" in output
    assert "Stream events: 2" in output
    assert "Stream event types: task, statusUpdate" in output
    assert "Terminal observed: yes" in output


async def test_tool_doctor_peer_returns_structured_diagnostic(config, monkeypatch):
    async def fake_doctor(
        url, token=None, timeout_seconds=None, live_probe=False, stream_probe=False,
        stream_probe_timeout=None, stream_probe_max_events=None, probe_message=None,
    ):
        assert url == "http://remote.test"
        assert token == "tool-token"
        assert timeout_seconds == 5
        assert live_probe is False
        assert stream_probe is False
        assert probe_message is None
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
            "live_probe": {"enabled": False, "attempted": False},
            "stream_probe": {"enabled": False, "attempted": False},
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
    assert payload["live_probe"] == {"enabled": False, "attempted": False}
    assert payload["stream_probe"] == {"enabled": False, "attempted": False}
    assert "tool-token" not in raw


async def test_tool_doctor_peer_live_probe_true(config, monkeypatch):
    async def fake_doctor(
        url, token=None, timeout_seconds=None, live_probe=False, stream_probe=False,
        stream_probe_timeout=None, stream_probe_max_events=None, probe_message=None,
    ):
        assert url == "http://remote.test"
        assert token == "tool-token"
        assert timeout_seconds == 5
        assert live_probe is True
        assert stream_probe is True
        assert stream_probe_timeout == 8
        assert stream_probe_max_events == 3
        assert probe_message == "tool ping"
        return {
            "ok": True,
            "status": "compatible",
            "url": url,
            "agent_card_url": f"{url}/.well-known/agent-card.json",
            "name": "Remote",
            "protocol": {"binding": "HTTP+JSON", "version": "1.0"},
            "capabilities": {"message_send": True},
            "warnings": [],
            "errors": [],
            "recommendations": [],
            "live_probe": {
                "enabled": True,
                "attempted": True,
                "message_send": True,
                "task_id": "task-tool",
                "task_get": True,
                "status": "passed",
                "warnings": [],
                "errors": [],
            },
            "stream_probe": {
                "enabled": True,
                "attempted": True,
                "status": "passed",
                "message_stream": True,
                "events_received": 1,
                "event_types": ["task"],
                "task_id": "task-tool-stream",
                "terminal_observed": False,
                "warnings": [],
                "errors": [],
            },
        }

    monkeypatch.setattr(tools, "diagnose_peer", fake_doctor)
    raw = await tools.a2a_doctor_peer({
        "agent_url": "http://remote.test",
        "token": "tool-token",
        "timeout_seconds": 5,
        "live_probe": True,
        "stream_probe": True,
        "stream_probe_timeout": 8,
        "stream_probe_max_events": 3,
        "probe_message": "tool ping",
    })
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["live_probe"]["enabled"] is True
    assert payload["live_probe"]["attempted"] is True
    assert payload["stream_probe"]["attempted"] is True
    assert "tool-token" not in raw
