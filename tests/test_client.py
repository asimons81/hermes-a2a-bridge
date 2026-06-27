import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hermes_a2a_bridge import client
from hermes_a2a_bridge.errors import ClientError


@pytest.fixture
async def remote_server():
    async def card(request):
        return web.json_response({"name": "Remote", "url": str(request.url.origin())})

    async def send(request):
        return web.json_response({"authorization": request.headers.get("Authorization"), "body": await request.json()})

    async def bad(request):
        return web.Response(text="not-json", content_type="text/plain")

    async def stream(request):
        if request.path.endswith(":subscribe"):
            assert request.headers.get("Last-Event-ID") in {None, "7"}
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'id: 8\nevent: message\ndata: {"task":{"id":"t1"}}\n\n')
        await response.write(b'id: 9\nevent: message\ndata: {"statusUpdate":{"taskId":"t1"}}\n\n')
        await response.write_eof()
        return response

    async def malformed_stream(request):
        return web.Response(text="data: {not-json}\n\n", content_type="text/event-stream")

    async def stream_error(request):
        return web.json_response({"error": "not subscribable"}, status=409)

    async def replay_gap(request):
        return web.json_response({
            "success": False,
            "error": "Requested replay cursor is no longer available because event history was pruned.",
            "code": "replay_gap",
            "task_id": "gap",
            "last_event_id": 1,
            "oldest_available_event_id": 5,
        }, status=409)

    async def file_metadata(request):
        if request.headers.get("Authorization") != "Bearer secret":
            return web.json_response({"success": False, "error": "bad secret", "code": "unauthorized"}, status=401)
        return web.json_response({
            "success": True,
            "file": {
                "fileId": request.match_info["file_id"],
                "name": "report.txt",
                "mimeType": "text/plain",
                "sizeBytes": 5,
                "sha256": "x" * 64,
            },
        })

    async def file_bytes(request):
        if request.headers.get("Authorization") != "Bearer secret":
            return web.json_response({
                "success": False,
                "error": f"Authorization: {request.headers.get('Authorization')}",
                "code": "unauthorized",
            }, status=401)
        if request.match_info["file_id"] == "file_remoteabcdefghijklmnop":
            return web.json_response({
                "success": False,
                "error": "This attachment is a metadata-only remote URL reference and has no local bytes.",
                "code": "file_bytes_unavailable",
            }, status=409)
        return web.Response(body=b"hello", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", send)
    app.router.add_post("/message:stream", stream)
    app.router.add_post("/tasks/t1:subscribe", stream)
    app.router.add_post("/tasks/bad:subscribe", malformed_stream)
    app.router.add_post("/tasks/done:subscribe", stream_error)
    app.router.add_post("/tasks/gap:subscribe", replay_gap)
    app.router.add_get("/files/{file_id}/metadata", file_metadata)
    app.router.add_get("/files/{file_id}", file_bytes)
    app.router.add_get("/bad/.well-known/agent-card.json", bad)
    server = TestServer(app)
    await server.start_server()
    yield server
    await server.close()


async def test_fetch_card_from_base_and_direct_url(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    assert (await client.fetch_agent_card(base))["name"] == "Remote"
    direct = str(remote_server.make_url("/.well-known/agent-card.json"))
    assert (await client.fetch_agent_card(direct))["name"] == "Remote"


async def test_discovery_preserves_limited_file_reference_metadata_without_side_effects(monkeypatch):
    called = False

    async def fail_download(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("discovery should not download files")

    async def card(request):
        return web.json_response({
            "name": "Remote",
            "url": str(request.url.origin()),
            "metadata": {
                "hermesA2ABridge": {
                    "fileReferences": {
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
                }
            },
        })

    monkeypatch.setattr(client, "download_file", fail_download)
    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    server = TestServer(app)
    await server.start_server()
    try:
        discovered = await client.fetch_agent_card(str(server.make_url("")).rstrip("/"))
        file_refs = discovered["metadata"]["hermesA2ABridge"]["fileReferences"]
        assert file_refs["supported"] is True
        assert file_refs["scope"] == "pre_staged_local_file_id_references_only"
        assert client.agent_endpoint(discovered) == str(server.make_url("")).rstrip("/")
        assert called is False
    finally:
        await server.close()


async def test_send_attaches_bearer_only_when_provided(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    assert (await client.send_message(base, "hi"))["authorization"] is None
    assert (await client.send_message(base, "hi", "secret"))["authorization"] == "Bearer [REDACTED]"
    assert (await client.send_message(base + "/", "hi"))["body"]["message"]["parts"][0]["text"] == "hi"


async def test_send_preserves_structured_data_parts(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    sent = await client.send_message(base, data={"alpha": 1, "items": [2]})
    part = sent["body"]["message"]["parts"][0]
    assert part == {"data": {"alpha": 1, "items": [2]}}

    mixed = await client.send_message(base, "hi", data=[{"x": True}])
    assert mixed["body"]["message"]["parts"][0]["text"] == "hi"
    assert mixed["body"]["message"]["parts"][1]["data"] == [{"x": True}]


async def test_send_builds_stored_file_id_parts_only(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    file_id = "file_abcdefghijklmnopqrstuv"
    sent = await client.send_message(base, "analyze this", file_ids=[file_id])
    parts = sent["body"]["message"]["parts"]
    assert parts == [
        {"text": "analyze this", "mediaType": "text/plain"},
        {"file": {"fileId": file_id}},
    ]
    serialized = str(parts)
    assert "uri" not in serialized
    assert "bytes" not in serialized
    assert "path" not in serialized
    assert "sourceUrl" not in serialized


async def test_stream_builds_stored_file_id_parts_only():
    seen = {}

    async def stream(request):
        seen["body"] = await request.json()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'id: 1\nevent: message\ndata: {"task":{"id":"t1"}}\n\n')
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/message:stream", stream)
    server = TestServer(app)
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        file_id = "file_abcdefghijklmnopqrstuv"
        events = [event async for event in client.stream_message(base, "stream this", file_ids=[file_id])]
        assert events[0]["data"]["task"]["id"] == "t1"
        assert seen["body"]["message"]["parts"] == [
            {"text": "stream this", "mediaType": "text/plain"},
            {"file": {"fileId": file_id}},
        ]
    finally:
        await server.close()


async def test_multiple_file_ids_preserve_order(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    first = "file_abcdefghijklmnopqrstuv"
    second = "file_bcdefghijklmnopqrstuvw"
    sent = await client.send_message(base, "compare", file_ids=[first, second])
    assert sent["body"]["message"]["parts"][1:] == [
        {"file": {"fileId": first}},
        {"file": {"fileId": second}},
    ]


@pytest.mark.parametrize("value", [
    "file_short",
    r"C:\Users\asimo\report.txt",
    "https://example.test/report.pdf",
])
async def test_invalid_file_ids_fail_before_request(remote_server, value):
    base = str(remote_server.make_url("")).rstrip("/")
    with pytest.raises(ClientError) as caught:
        await client.send_message(base, "hi", file_ids=[value])
    assert caught.value.code == "invalid_file_id"


async def test_invalid_response_is_clean_error(remote_server):
    with pytest.raises(ClientError, match="invalid JSON"):
        await client.fetch_agent_card(str(remote_server.make_url("/bad")))


async def test_network_error_is_clean():
    with pytest.raises(ClientError, match="request failed"):
        await client.fetch_agent_card("http://127.0.0.1:1")


async def test_stream_generators_parse_sse_events(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    message_events = [event async for event in client.stream_message(base, "hello", "secret")]
    subscribe_events = [event async for event in client.subscribe_task(base, "t1", "secret", 7)]
    assert message_events[0] == {"id": 8, "event": "message", "data": {"task": {"id": "t1"}}}
    assert subscribe_events[-1]["data"]["statusUpdate"]["taskId"] == "t1"


async def test_client_preserves_data_artifacts_in_stream_events():
    event = client._parse_sse_event(None, "message", [
        '{"artifactUpdate":{"taskId":"t1","contextId":"c1","artifact":{"artifactId":"a1","parts":[{"data":{"answer":42}}]}}}'
    ])
    assert event["data"]["artifactUpdate"]["artifact"]["parts"][0]["data"] == {"answer": 42}


async def test_client_preserves_file_artifacts_and_does_not_download_uri(monkeypatch):
    called = False

    async def fail_download(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("download should not be called")

    monkeypatch.setattr(client, "download_file", fail_download)
    event = client._parse_sse_event(None, "message", [
        '{"artifactUpdate":{"taskId":"t1","contextId":"c1","artifact":{"artifactId":"a1","parts":[{"file":{"fileId":"file_abcdefghijklmnopqrstuv","name":"report.txt","mimeType":"text/plain","sizeBytes":5,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","uri":"http://127.0.0.1:8765/files/file_abcdefghijklmnopqrstuv"}}]}}}'
    ])
    file = event["data"]["artifactUpdate"]["artifact"]["parts"][0]["file"]
    assert file["fileId"] == "file_abcdefghijklmnopqrstuv"
    assert client.extract_file_artifacts(event) == [file]
    assert called is False


async def test_client_preserves_remote_url_file_artifacts_and_does_not_download_source_url(monkeypatch):
    called = False

    async def fail_download(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("download should not be called")

    monkeypatch.setattr(client, "download_file", fail_download)
    event = client._parse_sse_event(None, "message", [
        '{"artifactUpdate":{"taskId":"t1","contextId":"c1","artifact":{"artifactId":"a1","parts":[{"file":{"fileId":"file_remoteabcdefghijklmnop","name":"report.pdf","mimeType":"application/pdf","metadataOnly":true,"bytesAvailable":false,"sourceUrl":"https://example.test/report.pdf"}}]}}}'
    ])
    file = event["data"]["artifactUpdate"]["artifact"]["parts"][0]["file"]
    assert file["metadataOnly"] is True
    assert file["sourceUrl"] == "https://example.test/report.pdf"
    assert client.extract_file_artifacts(event) == [file]
    assert called is False


async def test_stream_generators_report_malformed_sse_and_json_errors(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    with pytest.raises(ClientError, match="malformed SSE JSON"):
        _ = [event async for event in client.subscribe_task(base, "bad")]
    with pytest.raises(ClientError, match="HTTP 409.*not subscribable"):
        _ = [event async for event in client.subscribe_task(base, "done")]


async def test_client_exposes_structured_replay_gap(remote_server):
    base = str(remote_server.make_url("")).rstrip("/")
    with pytest.raises(ClientError, match="Replay cursor expired") as caught:
        _ = [event async for event in client.subscribe_task(base, "gap", last_event_id=1)]
    assert caught.value.status == 409
    assert caught.value.code == "replay_gap"
    assert caught.value.payload["oldest_available_event_id"] == 5


async def test_file_metadata_and_download_client_helpers(remote_server, tmp_path):
    base = str(remote_server.make_url("")).rstrip("/")
    metadata = await client.get_file_metadata(base, "file_abcdefghijklmnopqrstuv", "secret")
    assert metadata["fileId"] == "file_abcdefghijklmnopqrstuv"
    assert "storage_path" not in metadata

    output = tmp_path / "download.txt"
    body = await client.download_file(base, "file_abcdefghijklmnopqrstuv", "secret", output)
    assert body == b"hello"
    assert output.read_bytes() == b"hello"

    with pytest.raises(ClientError) as caught:
        await client.download_file(base, "file_abcdefghijklmnopqrstuv", "wrong-token")
    assert caught.value.status == 401
    assert "wrong-token" not in str(caught.value)
    assert "wrong-token" not in str(caught.value.payload)

    with pytest.raises(ClientError) as unavailable:
        await client.download_file(base, "file_remoteabcdefghijklmnop", "secret")
    assert unavailable.value.status == 409
    assert unavailable.value.code == "file_bytes_unavailable"


async def test_discovery_allows_0_3_only_card_but_endpoint_rejects_operations():
    async def card(request):
        return web.json_response({
            "name": "Legacy 0.3 Peer",
            "description": "Only advertises unsupported 0.3 REST.",
            "url": str(request.url.origin()) + "/v1",
            "version": "0.3-fixture",
            "protocolVersion": "0.3",
            "preferredTransport": "HTTP+JSON",
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        })

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    server = TestServer(app)
    await server.start_server()
    try:
        discovered = await client.fetch_agent_card(str(server.make_url("")).rstrip("/"))
        assert discovered["protocolVersion"] == "0.3"
        with pytest.raises(ClientError) as caught:
            client.agent_endpoint(discovered)
        assert caught.value.code == "unsupported_protocol_version"
        assert caught.value.payload == {
            "success": False,
            "code": "unsupported_protocol_version",
            "error": client.UNSUPPORTED_0_3_MESSAGE,
            "protocol_version": "0.3",
        }
    finally:
        await server.close()
