from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


TOKEN_QUERY_KEYS = {
    "access_token",
    "api_key",
    "auth",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "session",
    "sig",
    "signature",
    "token",
}
SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key"}
WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>]+")
UNIX_LOCAL_PATH_RE = re.compile(r"(?<!https:)\/(?:home|tmp|mnt|Users|var\/folders)\/[^\s\"'<>]+")


def _redact_local_paths(value: str) -> str:
    value = WINDOWS_PATH_RE.sub("[LOCAL_PATH]", value)
    return UNIX_LOCAL_PATH_RE.sub("[LOCAL_PATH]", value)


def sanitize_path_qs(path_qs: str) -> str:
    parts = urlsplit(path_qs)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in TOKEN_QUERY_KEYS:
            query.append((key, "[REDACTED]"))
        else:
            query.append((key, _redact_local_paths(value)))
    return urlunsplit(("", "", _redact_local_paths(parts.path), urlencode(query), ""))


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = _redact_local_paths(str(value))
    return sanitized


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_local_paths(value)
    return value


def sanitize_text(text: str) -> str:
    text = _redact_local_paths(text)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    return text


@dataclass
class RawCaptureHarness:
    json_response: dict[str, Any]
    sse_events: list[dict[str, Any]] = field(default_factory=list)
    error_response: dict[str, Any] | None = None
    captures: list[dict[str, Any]] = field(default_factory=list)
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

    async def _capture(self, request: web.Request, *, response_body: str | None = None) -> None:
        body_text = await request.text()
        try:
            body: Any = sanitize_payload(json.loads(body_text)) if body_text else None
        except json.JSONDecodeError:
            body = sanitize_text(body_text)
        self.captures.append({
            "request": {
                "method": request.method,
                "path": sanitize_path_qs(request.path_qs),
                "headers": sanitize_headers(dict(request.headers)),
                "body": body,
            },
            "response": {"body": sanitize_text(response_body)} if response_body is not None else None,
        })

    async def _agent_card(self, request: web.Request) -> web.Response:
        base = str(request.url.with_path("").with_query("")).rstrip("/")
        payload = {
            "name": "Raw Capture Harness Agent",
            "description": "Local test-only capture peer.",
            "version": "1.0.0-capture",
            "supportedInterfaces": [{
                "url": base,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }],
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{"id": "capture", "name": "Capture", "description": "Capture requests.", "tags": ["test"]}],
        }
        return web.json_response(payload)

    async def _send(self, request: web.Request) -> web.Response:
        payload = self.error_response or self.json_response
        await self._capture(request, response_body=json.dumps(payload, separators=(",", ":")))
        status = 400 if self.error_response else 200
        return web.json_response(payload, status=status)

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        await self._capture(request, response_body="".join(self._sse_frames()))
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for frame in self._sse_frames():
            await response.write(frame.encode("utf-8"))
        await response.write_eof()
        return response

    async def _task(self, request: web.Request) -> web.Response:
        await self._capture(request, response_body=json.dumps(self.json_response, separators=(",", ":")))
        return web.json_response(self.json_response)

    async def _fallback(self, request: web.Request) -> web.Response:
        payload = {"success": False, "code": "not_found", "error": "No capture route matched"}
        await self._capture(request, response_body=json.dumps(payload, separators=(",", ":")))
        return web.json_response(payload, status=404)

    def _sse_frames(self) -> list[str]:
        frames = []
        for index, event in enumerate(self.sse_events, start=1):
            frames.append(f"id: {index}\nevent: message\ndata: {json.dumps(event, separators=(',', ':'))}\n\n")
        return frames
