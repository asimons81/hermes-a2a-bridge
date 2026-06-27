"""Minimal HTTP+JSON A2A client."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .auth import redact_secrets
from .errors import ClientError
from .files import FILE_ID_RE

DEFAULT_TIMEOUT_SECONDS = 30
UNSUPPORTED_0_3_MESSAGE = (
    "Peer advertises A2A 0.3 REST behavior, which Hermes A2A Bridge does not implement. "
    "This bridge supports its documented A2A 1.0 HTTP+JSON text and data-part subset."
)


def require_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ClientError("A valid HTTP(S) URL is required")
    return url.rstrip("/")


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/a2a+json, application/json",
        "A2A-Version": "1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _redact_payload(value: Any, token: str | None) -> Any:
    if isinstance(value, dict):
        return {key: _redact_payload(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item, token) for item in value]
    if isinstance(value, str):
        return redact_secrets(value, token)
    return value


def _error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "Remote request failed"
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or payload.get("message") or "Remote request failed")
    return str(error or payload.get("message") or "Remote request failed")


async def _json_response(response: aiohttp.ClientResponse, token: str | None = None) -> Any:
    try:
        payload = json.loads(await response.text())
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        message = f"Remote agent returned invalid JSON (HTTP {response.status})"
        raise ClientError(
            message, status=response.status,
            payload={"success": False, "code": "invalid_json", "error": message},
        ) from exc
    payload = _redact_payload(payload, token)
    if response.status >= 400:
        message = _error_message(payload)
        code = _error_code(payload)
        prefix = "Replay cursor expired" if code == "replay_gap" else "Remote agent error"
        raise ClientError(
            f"{prefix} (HTTP {response.status}): {message}",
            status=response.status,
            payload=payload if isinstance(payload, dict) else None,
        )
    return payload


def _error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("code"), str):
        return payload["code"]
    error = payload.get("error")
    if isinstance(error, dict):
        for detail in error.get("details", []):
            if isinstance(detail, dict) and detail.get("reason"):
                return str(detail["reason"]).lower()
        if isinstance(error.get("status"), str):
            return error["status"].lower()
    return None


def _unsupported_protocol_error(protocol_version: str = "0.3") -> ClientError:
    return ClientError(
        UNSUPPORTED_0_3_MESSAGE,
        payload={
            "success": False,
            "code": "unsupported_protocol_version",
            "error": UNSUPPORTED_0_3_MESSAGE,
            "protocol_version": protocol_version,
        },
    )


def _protocol_major(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.split(".", 1)[0]


def _is_http_json(interface: dict[str, Any]) -> bool:
    binding = interface.get("protocolBinding") or interface.get("transport")
    return binding in {None, "HTTP+JSON"}


def _has_declared_endpoint(card: dict[str, Any]) -> bool:
    if isinstance(card.get("url"), str) and card["url"]:
        return True
    for field in ("supportedInterfaces", "additionalInterfaces"):
        interfaces = card.get(field)
        if isinstance(interfaces, list) and any(
            isinstance(item, dict) and item.get("url") for item in interfaces
        ):
            return True
    return False


def _unsupported_protocol_version(card: dict[str, Any]) -> str | None:
    versions: list[str] = []
    if isinstance(card.get("protocolVersion"), str):
        versions.append(card["protocolVersion"])
    for field in ("supportedInterfaces", "additionalInterfaces"):
        interfaces = card.get(field)
        if not isinstance(interfaces, list):
            continue
        for interface in interfaces:
            if isinstance(interface, dict) and isinstance(interface.get("protocolVersion"), str):
                versions.append(interface["protocolVersion"])
    for version in versions:
        if _protocol_major(version) == "0":
            return version
    if str(card.get("preferredTransport", "")).upper() == "JSONRPC":
        return "0.3"
    return None


def agent_endpoint(card: dict[str, Any]) -> str:
    for field in ("supportedInterfaces", "additionalInterfaces"):
        interfaces = card.get(field)
        if not isinstance(interfaces, list):
            continue
        for interface in interfaces:
            if not isinstance(interface, dict) or not interface.get("url"):
                continue
            if _is_http_json(interface) and _protocol_major(interface.get("protocolVersion") or "1.0") == "1":
                return require_http_url(interface["url"])
    unsupported = _unsupported_protocol_version(card)
    if unsupported:
        raise _unsupported_protocol_error(unsupported)
    if isinstance(card.get("url"), str) and card["url"]:
        return require_http_url(card["url"])
    raise ClientError("Remote Agent Card does not declare an HTTP+JSON endpoint")


async def fetch_agent_card(url: str, *, session: aiohttp.ClientSession | None = None) -> dict[str, Any]:
    target = require_http_url(url)
    path = urlparse(target).path
    if not path.endswith("/.well-known/agent-card.json"):
        target += "/.well-known/agent-card.json"
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS))
    try:
        async with session.get(target) as response:
            card = await _json_response(response)
        if not isinstance(card, dict) or not card.get("name"):
            raise ClientError("Remote Agent Card is missing required name")
        if not _has_declared_endpoint(card):
            raise ClientError("Remote Agent Card does not declare an endpoint")
        return card
    except aiohttp.ClientError as exc:
        raise ClientError(f"Agent Card request failed: {exc}") from exc
    finally:
        if own:
            await session.close()


def validate_file_ids(file_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    values = list(file_ids or [])
    for file_id in values:
        if not isinstance(file_id, str) or not FILE_ID_RE.fullmatch(file_id):
            raise ClientError(
                "File IDs must be stored Hermes file references shaped like file_ followed by opaque ID characters.",
                payload={
                    "success": False,
                    "error": "Invalid stored file ID reference.",
                    "code": "invalid_file_id",
                },
            )
    return values


def _message_parts(
    text: str | None = None,
    data: dict[str, Any] | list[Any] | None = None,
    file_ids: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"text": text, "mediaType": "text/plain"})
    if data is not None:
        if not isinstance(data, (dict, list)):
            raise ClientError("data must be a JSON object or array")
        parts.append({"data": data})
    for file_id in validate_file_ids(file_ids):
        parts.append({"file": {"fileId": file_id}})
    if not parts:
        raise ClientError("A text message, JSON data object/array, or stored file ID is required")
    return parts


def extract_file_artifacts(task_or_event: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []

    def collect_from_artifact(artifact: Any) -> None:
        if not isinstance(artifact, dict):
            return
        parts = artifact.get("parts")
        if not isinstance(parts, list):
            return
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("file"), dict):
                files.append(part["file"])

    if not isinstance(task_or_event, dict):
        return files
    if isinstance(task_or_event.get("task"), dict):
        task_or_event = task_or_event["task"]
    data = task_or_event.get("data")
    if isinstance(data, dict):
        files.extend(extract_file_artifacts(data))
    for artifact in task_or_event.get("artifacts", []) if isinstance(task_or_event.get("artifacts"), list) else []:
        collect_from_artifact(artifact)
    update = task_or_event.get("artifactUpdate")
    if isinstance(update, dict):
        collect_from_artifact(update.get("artifact"))
    return files


async def send_message(
    base_url: str, text: str | None = None, token: str | None = None, context_id: str | None = None,
    timeout_seconds: int | None = None, *, metadata: dict[str, Any] | None = None,
    data: dict[str, Any] | list[Any] | None = None,
    file_ids: list[str] | tuple[str, ...] | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    url = require_http_url(base_url) + "/message:send"
    message = {
        "messageId": str(uuid.uuid4()), "role": "ROLE_USER",
        "parts": _message_parts(text, data, file_ids),
    }
    if context_id:
        message["contextId"] = context_id
    if metadata is not None:
        message["metadata"] = metadata
    own = session is None
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
    session = session or aiohttp.ClientSession(timeout=timeout)
    try:
        headers = {**_headers(token), "Content-Type": "application/a2a+json"}
        async with session.post(url, json={"message": message}, headers=headers) as response:
            payload = await _json_response(response, token)
            if isinstance(payload, dict) and isinstance(payload.get("task"), dict):
                return payload["task"]
            return payload
    except aiohttp.ClientError as exc:
        raise ClientError(f"Send request failed: {exc}") from exc
    finally:
        if own:
            await session.close()


async def stream_message(
    base_url: str,
    text: str | None = None,
    token: str | None = None,
    context_id: str | None = None,
    *,
    metadata: dict[str, Any] | None = None,
    data: dict[str, Any] | list[Any] | None = None,
    file_ids: list[str] | tuple[str, ...] | None = None,
    session: aiohttp.ClientSession | None = None,
):
    message = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": _message_parts(text, data, file_ids),
    }
    if context_id:
        message["contextId"] = context_id
    if metadata is not None:
        message["metadata"] = metadata
    async for event in _stream_request(
        "POST",
        f"{require_http_url(base_url)}/message:stream",
        token,
        json_body={"message": message},
        session=session,
    ):
        yield event


async def subscribe_task(
    base_url: str,
    task_id: str,
    token: str | None = None,
    last_event_id: int | None = None,
    *,
    session: aiohttp.ClientSession | None = None,
):
    async for event in _stream_request(
        "POST",
        f"{require_http_url(base_url)}/tasks/{task_id}:subscribe",
        token,
        extra_headers={"Last-Event-ID": str(last_event_id)} if last_event_id is not None else None,
        session=session,
    ):
        yield event


async def _stream_request(
    method: str,
    url: str,
    token: str | None,
    *,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    session: aiohttp.ClientSession | None = None,
):
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS))
    data_lines: list[str] = []
    event_id: str | None = None
    event_name = "message"
    try:
        headers = {**_headers(token), **(extra_headers or {})}
        if json_body is not None:
            headers["Content-Type"] = "application/a2a+json"
        async with session.request(method, url, headers=headers, json=json_body) as response:
            if response.status >= 400:
                await _json_response(response, token)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "text/event-stream":
                raise ClientError(f"Remote agent returned a non-SSE response (HTTP {response.status})")
            async for raw_line in response.content:
                try:
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError as exc:
                    raise ClientError("Remote agent returned malformed SSE data") from exc
                if not line.strip():
                    if data_lines:
                        yield _parse_sse_event(event_id, event_name, data_lines, response.status)
                        data_lines = []
                        event_id = None
                        event_name = "message"
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
                elif line.startswith("id:"):
                    event_id = line[3:].lstrip(" ")
                elif line.startswith("event:"):
                    event_name = line[6:].lstrip(" ") or "message"
            if data_lines:
                yield _parse_sse_event(event_id, event_name, data_lines, response.status)
    except asyncio.TimeoutError as exc:
        raise ClientError("Stream request timed out") from exc
    except aiohttp.ClientError as exc:
        raise ClientError(f"Stream request failed: {exc}") from exc
    finally:
        if own:
            await session.close()


def _parse_sse_event(
    event_id: str | None, event_name: str, lines: list[str], status: int = 200,
) -> dict[str, Any]:
    try:
        value = json.loads("\n".join(lines))
    except (json.JSONDecodeError, ValueError) as exc:
        message = "Remote agent returned malformed SSE JSON"
        raise ClientError(
            message, status=status,
            payload={"success": False, "code": "malformed_sse", "error": message},
        ) from exc
    if not isinstance(value, dict):
        message = "Remote agent returned a non-object SSE event"
        raise ClientError(
            message, status=status,
            payload={"success": False, "code": "malformed_sse", "error": message},
        )
    try:
        parsed_id = int(event_id) if event_id not in {None, ""} else None
    except ValueError as exc:
        message = "Remote agent returned a malformed SSE event ID"
        raise ClientError(
            message, status=status,
            payload={"success": False, "code": "malformed_sse", "error": message},
        ) from exc
    return {"id": parsed_id, "event": event_name, "data": value}


async def get_task(base_url: str, task_id: str, token: str | None = None, *, session=None) -> dict[str, Any]:
    return await _request("GET", f"{require_http_url(base_url)}/tasks/{task_id}", token, session=session)


async def list_tasks(base_url: str, token: str | None = None, status: str | None = None, *, session=None) -> list[dict[str, Any]]:
    params = {"status": status} if status else None
    return await _request("GET", f"{require_http_url(base_url)}/tasks", token, params=params, session=session)


async def cancel_task(base_url: str, task_id: str, token: str | None = None, *, session=None) -> dict[str, Any]:
    return await _request("POST", f"{require_http_url(base_url)}/tasks/{task_id}:cancel", token, session=session)


async def get_file_metadata(
    base_url: str,
    file_id: str,
    token: str | None = None,
    *,
    session=None,
) -> dict[str, Any]:
    payload = await _request(
        "GET",
        f"{require_http_url(base_url)}/files/{file_id}/metadata",
        token,
        session=session,
    )
    if isinstance(payload, dict) and isinstance(payload.get("file"), dict):
        return payload["file"]
    return payload


async def download_file(
    base_url: str,
    file_id: str,
    token: str | None = None,
    output_path: str | Path | None = None,
    *,
    session=None,
) -> bytes:
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS))
    try:
        url = f"{require_http_url(base_url)}/files/{file_id}"
        async with session.get(url, headers=_headers(token)) as response:
            if response.status >= 400:
                await _json_response(response, token)
            body = await response.read()
        if output_path is not None:
            target = Path(output_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        return body
    except aiohttp.ClientError as exc:
        raise ClientError(f"File download failed: {exc}") from exc
    finally:
        if own:
            await session.close()


async def _request(method: str, url: str, token: str | None, *, params=None, session=None):
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS))
    try:
        async with session.request(method, url, headers=_headers(token), params=params) as response:
            return await _json_response(response, token)
    except aiohttp.ClientError as exc:
        raise ClientError(f"Remote request failed: {exc}") from exc
    finally:
        if own:
            await session.close()
