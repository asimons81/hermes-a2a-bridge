"""Deterministic local HTTP+JSON A2A peer used by interoperability tests."""

from __future__ import annotations

from aiohttp import web


def _task(state: str = "TASK_STATE_COMPLETED") -> dict:
    task = {
        "id": "external-task-1",
        "contextId": "external-context-1",
        "status": {"state": state, "timestamp": "2026-06-24T12:00:00Z"},
        "metadata": {"server": "fake-external-a2a"},
    }
    if state == "TASK_STATE_COMPLETED":
        task["artifacts"] = [{
            "artifactId": "external-artifact-1",
            "parts": [{"text": "external result", "mediaType": "text/plain"}],
        }]
    return task


def create_fake_external_app() -> web.Application:
    async def card(request: web.Request) -> web.Response:
        origin = f"{request.scheme}://{request.host}"
        return web.json_response({
            "name": "Fake External A2A",
            "description": "A local deterministic interoperability peer.",
            "version": "1.0.0-test",
            "supportedInterfaces": [{
                "url": origin,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }],
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{
                "id": "echo", "name": "Echo", "description": "Return deterministic text.",
                "tags": ["test", "text"],
            }],
            "xExternalField": {"ignoredByTolerantClients": True},
        })

    async def send(request: web.Request) -> web.Response:
        payload = await request.json()
        text = payload["message"]["parts"][0]["text"]
        if text == "fail":
            return web.json_response({
                "error": {
                    "code": 400,
                    "status": "INVALID_ARGUMENT",
                    "message": "The fake external server rejected the message",
                    "details": [{
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "CONTENT_TYPE_NOT_SUPPORTED",
                        "domain": "a2a-protocol.org",
                    }],
                }
            }, status=400, content_type="application/a2a+json")
        return web.json_response({"task": _task()}, content_type="application/a2a+json")

    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8"})
        await response.prepare(request)
        await response.write(b": fake heartbeat\n\n")
        await response.write(
            b"id: 41\nretry: 1000\nevent: external-task\ndata: {\"task\":\n"
            b"data: {\"id\":\"external-task-1\",\"status\":{\"state\":\"TASK_STATE_SUBMITTED\"}}}\n   \n"
        )
        await response.write(
            b"id: 42\nevent: message\ndata: {\"statusUpdate\":{\"taskId\":\"external-task-1\","
            b"\"contextId\":\"external-context-1\",\"status\":{\"state\":\"TASK_STATE_COMPLETED\"}}}\n\n"
        )
        await response.write_eof()
        return response

    async def malformed_stream(request: web.Request) -> web.Response:
        return web.Response(
            body=b"id: 9\nevent: message\ndata: {not-json}\n\n",
            headers={"Content-Type": "text/event-stream"},
        )

    async def get_task(request: web.Request) -> web.Response:
        return web.json_response(_task(), content_type="application/a2a+json")

    async def cancel_task(request: web.Request) -> web.Response:
        return web.json_response(_task("TASK_STATE_CANCELED"), content_type="application/a2a+json")

    app = web.Application()
    app.router.add_get("/.well-known/agent-card.json", card)
    app.router.add_post("/message:send", send)
    app.router.add_post("/message:stream", stream)
    app.router.add_post("/malformed/message:stream", malformed_stream)
    app.router.add_get("/tasks/{task_id}", get_task)
    app.router.add_post("/tasks/{task_id}:cancel", cancel_task)
    return app
