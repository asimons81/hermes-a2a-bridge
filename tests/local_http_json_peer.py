from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from raw_capture_harness import sanitize_headers, sanitize_path_qs, sanitize_payload, sanitize_text


TASK_ID = "local-peer-task-1"
CONTEXT_ID = "local-peer-context-1"


def _task(task_id: str = TASK_ID, state: str = "TASK_STATE_COMPLETED") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": task_id,
        "contextId": CONTEXT_ID,
        "status": {"state": state},
        "history": [],
        "artifacts": [{
            "artifactId": "local-peer-artifact-1",
            "parts": [{"text": "local peer result", "mediaType": "text/plain"}],
            "metadata": {"source": "local-http-json-peer"},
        }],
        "metadata": {"source": "local-http-json-peer"},
    }
    if state != "TASK_STATE_COMPLETED":
        payload.pop("artifacts", None)
    return payload


def _structured_error(
    *,
    message: str,
    bridge_code: str,
    reason: str = "LOCAL_COMPATIBILITY_ERROR",
) -> dict[str, Any]:
    return {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": message,
            "details": [{
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": reason,
                "domain": "local-http-json-peer.test",
                "metadata": {"bridgeCode": bridge_code},
            }],
        }
    }


def _has_file_like_part(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    parts = payload.get("message", {}).get("parts", [])
    if not isinstance(parts, list):
        return False
    file_keys = {"file", "raw", "url", "filename", "blob"}
    file_kinds = {"file", "image", "audio", "video"}
    for part in parts:
        if not isinstance(part, dict):
            continue
        if file_keys.intersection(part):
            return True
        if part.get("kind") in file_kinds or part.get("type") in file_kinds:
            return True
    return False


@dataclass
class LocalHttpJsonPeer:
    """Deterministic test-only HTTP+JSON 1.0 compatibility peer."""

    captures: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    client: TestClient | None = None

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/.well-known/agent-card.json", self._agent_card)
        app.router.add_post("/message:send", self._send)
        app.router.add_post("/message:stream", self._stream)
        app.router.add_get("/tasks/{task_id}", self._task)
        app.router.add_route("*", "/{tail:.*}", self._fallback)
        return app

    async def start(self) -> str:
        self.client = TestClient(TestServer(self.app()))
        await self.client.start_server()
        return str(self.client.make_url("")).rstrip("/")

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()

    async def _capture(
        self,
        label: str,
        request: web.Request,
        *,
        response_body: Any | str | None = None,
    ) -> None:
        body_text = await request.text()
        try:
            body: Any = sanitize_payload(json.loads(body_text)) if body_text else None
        except json.JSONDecodeError:
            body = sanitize_text(body_text)
        if isinstance(response_body, str):
            response_text = sanitize_text(response_body)
        elif response_body is None:
            response_text = None
        else:
            response_text = sanitize_text(json.dumps(response_body, separators=(",", ":")))
        self.captures.setdefault(label, []).append({
            "request": {
                "method": request.method,
                "path": sanitize_path_qs(request.path_qs),
                "headers": sanitize_headers(dict(request.headers)),
                "body": body,
            },
            "response": {"body": response_text} if response_text is not None else None,
        })

    async def _agent_card(self, request: web.Request) -> web.Response:
        base = str(request.url.with_path("").with_query("")).rstrip("/")
        payload = {
            "name": "Local HTTP JSON Compatibility Peer",
            "description": "Deterministic test-only peer for Hermes HTTP+JSON 1.0 subset capture.",
            "version": "1.0.0-local-compat",
            "supportedInterfaces": [{
                "url": base,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }],
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "skills": [{
                "id": "local-compat",
                "name": "Local Compatibility",
                "description": "Deterministic text and JSON test behavior.",
                "tags": ["test", "compatibility"],
            }],
        }
        await self._capture("discover", request, response_body=payload)
        return web.json_response(payload)

    async def _send(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            error = _structured_error(
                message="Request body must be valid JSON.",
                bridge_code="malformed_json",
            )
            await self._capture("structured_error", request, response_body=error)
            return web.json_response(error, status=400, content_type="application/a2a+json")
        if _has_file_like_part(payload):
            error = _structured_error(
                message="File parts are not supported by this local compatibility peer.",
                bridge_code="unsupported_part_type",
                reason="CONTENT_TYPE_NOT_SUPPORTED",
            )
            await self._capture("file_part_rejection", request, response_body=error)
            return web.json_response(error, status=400, content_type="application/a2a+json")
        text = _first_text(payload)
        if text == "structured-error":
            error = _structured_error(
                message="Local compatibility peer returned a structured error.",
                bridge_code="local_peer_structured_error",
            )
            await self._capture("structured_error", request, response_body=error)
            return web.json_response(error, status=400, content_type="application/a2a+json")
        task = _task()
        self.tasks[TASK_ID] = task
        response = {"task": task}
        await self._capture("message_send", request, response_body=response)
        return web.json_response(response, content_type="application/a2a+json")

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        task = _task()
        self.tasks[TASK_ID] = task
        frames = [
            {"task": _task(TASK_ID, "TASK_STATE_SUBMITTED")},
            {
                "artifactUpdate": {
                    "taskId": TASK_ID,
                    "contextId": CONTEXT_ID,
                    "artifact": {
                        "artifactId": "local-peer-artifact-1",
                        "parts": [{"text": "local peer streamed result", "mediaType": "text/plain"}],
                    },
                    "lastChunk": True,
                    "metadata": {"source": "local-http-json-peer"},
                }
            },
            {
                "statusUpdate": {
                    "taskId": TASK_ID,
                    "contextId": CONTEXT_ID,
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "metadata": {"final": True},
                }
            },
        ]
        sse_body = _sse_frames(frames)
        await self._capture("message_stream", request, response_body=sse_body)
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(sse_body.encode("utf-8"))
        await response.write_eof()
        return response

    async def _task(self, request: web.Request) -> web.Response:
        task = self.tasks.get(request.match_info["task_id"]) or _task(request.match_info["task_id"])
        await self._capture("task_lookup", request, response_body=task)
        return web.json_response(task)

    async def _fallback(self, request: web.Request) -> web.Response:
        error = _structured_error(
            message="No local compatibility peer route matched.",
            bridge_code="not_found",
        )
        await self._capture("structured_error", request, response_body=error)
        return web.json_response(error, status=404)


def _first_text(payload: dict[str, Any]) -> str | None:
    parts = payload.get("message", {}).get("parts", [])
    if not isinstance(parts, list):
        return None
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            return part["text"]
    return None


def _sse_frames(events: list[dict[str, Any]]) -> str:
    frames = []
    for index, event in enumerate(events, start=1):
        frames.append(f"id: {index}\nevent: message\ndata: {json.dumps(event, separators=(',', ':'))}\n\n")
    return "".join(frames)
