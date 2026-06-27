"""Local-first aiohttp server implementing the bounded A2A subset."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from .auth import bearer_is_valid, redact_secrets
from .config import database_path, validate_server_bind
from .executor import ExecutorCanceled, ExecutorManager, execute
from . import files
from ._version import __version__
from .models import (
    Artifact,
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    build_agent_card,
)
from .operations import expire_cancellations, prune_events, recover_expired_leases, recover_stale
from .store import Store
from .streaming import EventBroker, TERMINAL_STATES, sse_response, write_sse

CONFIG_KEY = web.AppKey("config", dict)
STORE_KEY = web.AppKey("store", Store)
SEMAPHORE_KEY = web.AppKey("task_semaphore", asyncio.Semaphore)
EVENT_BROKER_KEY = web.AppKey("event_broker", EventBroker)
BACKGROUND_TASKS_KEY = web.AppKey("background_tasks", set)
EXECUTOR_MANAGER_KEY = web.AppKey("executor_manager", ExecutorManager)
INSTANCE_ID_KEY = web.AppKey("instance_id", str)

UNSUPPORTED_PART_MESSAGE = (
    "File parts are not supported yet. Hermes A2A Bridge currently supports text "
    "and structured JSON data parts."
)


class PartValidationError(ValueError):
    def __init__(self, message: str, code: str = "unsupported_message"):
        super().__init__(message)
        self.code = code


def wire(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True, mode="json")
    return value


def _json_error(message: str, status: int, code: str | None = None) -> web.Response:
    payload = {"success": False, "error": message}
    if code:
        payload["code"] = code
    return web.json_response(payload, status=status)


def _request_error(
    request: web.Request,
    message: str,
    status: int,
    code: str,
    *,
    reason: str = "INVALID_REQUEST",
    rpc_status: str = "INVALID_ARGUMENT",
) -> web.Response:
    """Use the A2A 1.0 error envelope only when the caller negotiates 1.x."""
    if request.headers.get("A2A-Version", "").startswith("1."):
        return web.json_response({
            "error": {
                "code": status,
                "status": rpc_status,
                "message": message,
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": reason,
                    "domain": "a2a-protocol.org",
                    "metadata": {"bridgeCode": code},
                }],
            }
        }, status=status, content_type="application/a2a+json")
    return _json_error(message, status, code)


@web.middleware
async def json_error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status >= 400:
            return _json_error(exc.reason or exc.text or "Request failed", exc.status, "http_error")
        raise
    except Exception as exc:
        token = request.app[CONFIG_KEY]["server"].get("auth_token") if CONFIG_KEY in request.app else None
        return _json_error(redact_secrets(exc, token), 500, "internal_error")


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in {"/health", "/.well-known/agent-card.json"}:
        return await handler(request)
    config = request.app[CONFIG_KEY]
    server = config["server"]
    if server.get("require_auth", True) and not bearer_is_valid(
        request.headers.get("Authorization"), server["auth_token"]
    ):
        return _json_error("Unauthorized", 401, "unauthorized")
    return await handler(request)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "version": __version__})


def _file_error(message: str, status: int, code: str) -> web.Response:
    return _json_error(message, status, code)


def _file_id_from_request(request: web.Request) -> str | web.Response:
    file_id = request.match_info["file_id"]
    if not files.FILE_ID_RE.fullmatch(file_id):
        return _file_error("Invalid file attachment id", 400, "invalid_file_id")
    return file_id


def _stored_attachment(
    request: web.Request,
    *,
    require_bytes: bool = False,
) -> tuple[dict[str, Any], Path] | web.Response:
    file_id = _file_id_from_request(request)
    if isinstance(file_id, web.Response):
        return file_id
    row = request.app[STORE_KEY].get_file_attachment(file_id)
    if row is None:
        return _file_error("File attachment metadata was not found", 404, "file_not_found")
    storage_path = row.get("storage_path")
    if not isinstance(storage_path, str) or not storage_path:
        if require_bytes and row.get("source") == "remote_url":
            return _file_error(
                "This attachment is a metadata-only remote URL reference and has no local bytes.",
                409,
                "file_bytes_unavailable",
            )
        if require_bytes:
            return _file_error("Stored file bytes are no longer available", 410, "file_bytes_missing")
        return row, Path()
    try:
        root = files.resolve_storage_root(request.app[CONFIG_KEY])
        path = files.validate_storage_path(root, storage_path)
    except files.FileAttachmentError:
        return _file_error("Stored file path is outside the configured storage root", 403, "unsafe_file_path")
    if path.is_symlink():
        return _file_error("Stored file path is unsafe", 403, "unsafe_file_path")
    if require_bytes:
        if not path.exists():
            return _file_error("Stored file bytes are no longer available", 410, "file_bytes_missing")
        if not path.is_file():
            return _file_error("Stored file bytes are not a regular file", 410, "file_bytes_missing")
        try:
            size = path.stat(follow_symlinks=False).st_size
        except OSError:
            return _file_error("Stored file bytes cannot be inspected", 410, "file_bytes_missing")
        try:
            files.validate_file_size(size, request.app[CONFIG_KEY])
        except files.FileAttachmentError:
            return _file_error("Stored file exceeds the configured file size limit", 413, "file_too_large")
        expected_size = row.get("size_bytes")
        if expected_size is not None and size != int(expected_size):
            return _file_error("Stored file size does not match metadata", 409, "file_size_mismatch")
        expected_sha = row.get("sha256")
        if expected_sha and files.sha256_file(path) != expected_sha:
            return _file_error("Stored file checksum does not match metadata", 409, "file_checksum_mismatch")
    return row, path


async def file_metadata_get(request: web.Request) -> web.Response:
    resolved = _stored_attachment(request)
    if isinstance(resolved, web.Response):
        return resolved
    row, _ = resolved
    return web.json_response({"success": True, "file": files.public_file_metadata(row)})


async def file_bytes_get(request: web.Request) -> web.Response:
    resolved = _stored_attachment(request, require_bytes=True)
    if isinstance(resolved, web.Response):
        return resolved
    row, path = resolved
    mime_type = row.get("mime_type") or "application/octet-stream"
    try:
        body = path.read_bytes()
    except OSError:
        return _file_error("Stored file bytes are no longer available", 410, "file_bytes_missing")
    safe_name = row.get("safe_filename") or row.get("filename") or "attachment"
    headers = {
        "Content-Disposition": files.safe_content_disposition(str(safe_name)),
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return web.Response(body=body, headers=headers, content_type=mime_type)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _part_kind(part: dict[str, Any]) -> str | None:
    return part.get("kind") or part.get("type")


def _file_part_error_for_shape(file_value: Any) -> tuple[str, str]:
    if not isinstance(file_value, dict):
        return "File references must be objects.", "invalid_file_reference"
    if any(key in file_value for key in ("bytes", "base64", "raw", "blob", "data")):
        return "Inline file bytes are not supported.", "unsupported_inline_file_bytes"
    if any(key in file_value for key in ("uri", "url", "fileUri", "sourceUrl")):
        return "Remote URL file references are not supported for inbound message parts.", "unsupported_remote_file_url"
    if any(key in file_value for key in ("path", "localPath", "filename")):
        return "Arbitrary local path file references are not supported.", "invalid_file_reference"
    if "fileId" not in file_value:
        return "Only stored fileId references are supported for inbound file parts.", "unsupported_file_reference"
    if set(file_value) != {"fileId"}:
        return "Only stored fileId references are supported for inbound file parts.", "unsupported_file_reference"
    return "Invalid file reference.", "invalid_file_reference"


def _safe_inbound_file_reference(
    config: dict[str, Any],
    store: Store,
    file_id: str,
) -> dict[str, Any]:
    if not isinstance(file_id, str) or not files.FILE_ID_RE.fullmatch(file_id):
        raise PartValidationError("Invalid stored file reference.", "invalid_file_reference")
    row = store.get_file_attachment(file_id)
    if row is None:
        raise PartValidationError("Stored file reference was not found.", "file_not_found")
    if row.get("source") == "remote_url":
        raise PartValidationError(
            "Remote URL file references are not supported for inbound message parts.",
            "unsupported_remote_file_url",
        )
    storage_path = row.get("storage_path")
    if not isinstance(storage_path, str) or not storage_path:
        raise PartValidationError("Stored file bytes are unavailable.", "file_bytes_unavailable")
    try:
        root = files.resolve_storage_root(config)
        path = files.validate_storage_path(root, storage_path)
    except files.FileAttachmentError as exc:
        raise PartValidationError("Stored file reference failed integrity validation.", "file_integrity_failed") from exc
    if path.is_symlink():
        raise PartValidationError("Stored file reference failed integrity validation.", "file_integrity_failed")
    if not path.exists() or not path.is_file():
        raise PartValidationError("Stored file bytes are unavailable.", "file_bytes_unavailable")
    try:
        size = path.stat(follow_symlinks=False).st_size
    except OSError as exc:
        raise PartValidationError("Stored file bytes are unavailable.", "file_bytes_unavailable") from exc
    try:
        files.validate_file_size(size, config)
    except files.FileAttachmentError as exc:
        raise PartValidationError("Stored file reference failed integrity validation.", "file_integrity_failed") from exc
    expected_size = row.get("size_bytes")
    if expected_size is None or size != int(expected_size):
        raise PartValidationError("Stored file reference failed integrity validation.", "file_integrity_failed")
    expected_sha = row.get("sha256")
    if not expected_sha:
        raise PartValidationError("Stored file reference failed integrity validation.", "file_integrity_failed")
    try:
        actual_sha = files.sha256_file(path)
    except OSError as exc:
        raise PartValidationError("Stored file bytes are unavailable.", "file_bytes_unavailable") from exc
    if actual_sha != expected_sha:
        raise PartValidationError("Stored file reference failed integrity validation.", "file_integrity_failed")
    public = files.public_file_metadata(row)
    return {
        "fileId": public["fileId"],
        "name": public.get("name"),
        "mimeType": public.get("mimeType"),
        "sizeBytes": public.get("sizeBytes"),
        "sha256": public.get("sha256"),
        "bytesAvailable": True,
        "source": "local",
    }


def _validate_raw_part(
    config: dict[str, Any],
    store: Store,
    index: int,
    part: Any,
) -> dict[str, Any] | None:
    if not isinstance(part, dict):
        raise PartValidationError(f"Part {index} must be an object")
    kind = _part_kind(part)
    if kind in {"image", "audio", "video"} or any(key in part for key in ("raw", "url", "filename", "blob")):
        raise PartValidationError(UNSUPPORTED_PART_MESSAGE, "unsupported_part_type")
    if kind == "file" or "file" in part:
        parts_config = config.get("parts", {})
        if not parts_config.get("allow_file_parts", False):
            raise PartValidationError(UNSUPPORTED_PART_MESSAGE, "unsupported_part_type")
        if not parts_config.get("allow_file_id_references", False):
            raise PartValidationError(
                "Stored file references are disabled by configuration.",
                "file_reference_disabled",
            )
        message, code = _file_part_error_for_shape(part.get("file"))
        file_value = part.get("file")
        if code != "invalid_file_reference" or not isinstance(file_value, dict) or set(file_value) != {"fileId"}:
            raise PartValidationError(message, code)
        safe = _safe_inbound_file_reference(config, store, file_value["fileId"])
        part.clear()
        part["file"] = {key: value for key, value in safe.items() if key not in {"source"} and value is not None}
        return safe
    if kind not in {None, "text", "data"}:
        raise PartValidationError(
            f"Unsupported part type at index {index}; supported part types are text and data.",
            "unsupported_part_type",
        )
    if "data" in part or kind == "data":
        parts_config = config.get("parts", {})
        if not parts_config.get("allow_data_parts", True):
            raise PartValidationError("Structured JSON data parts are disabled by configuration.")
        data = part.get("data")
        if not isinstance(data, (dict, list)):
            raise PartValidationError("Data parts must contain a JSON object or array.", "unsupported_part_type")
        max_bytes = int(parts_config.get("max_data_part_bytes", 65536))
        if _json_size(data) > max_bytes:
            raise PartValidationError(
                f"Data part at index {index} exceeds configured max_data_part_bytes ({max_bytes}).",
                "data_part_too_large",
            )
        return None
    if "text" not in part:
        raise PartValidationError(
            f"Unsupported part shape at index {index}; supported part types are text and data.",
            "unsupported_part_type",
        )
    return None


def _render_data_block(index: int, data: Any) -> str:
    pretty = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return f"Data part {index}:\n\n```json\n{pretty}\n```"


def _render_file_reference(index: int, file: dict[str, Any]) -> str:
    lines = [f"File reference {index}:"]
    for label, key in (
        ("fileId", "fileId"),
        ("name", "name"),
        ("mimeType", "mimeType"),
        ("sizeBytes", "sizeBytes"),
        ("sha256", "sha256"),
        ("bytesAvailable", "bytesAvailable"),
    ):
        if key in file:
            lines.append(f"- {label}: {file[key]}")
    return "\n".join(lines)


def _render_executor_prompt(message: Message) -> str:
    text_parts = [part.text for part in message.parts if part.kind == "text" and part.text]
    data_parts = [part.data for part in message.parts if part.kind == "data"]
    file_parts = [part.file for part in message.parts if part.kind == "file" and isinstance(part.file, dict)]
    if text_parts and not data_parts and not file_parts:
        return "\n".join(text_parts)
    sections: list[str] = []
    if text_parts:
        text = "\n".join(text_parts)
        sections.append(f"Text: {text}")
    for index, data in enumerate(data_parts, start=1):
        sections.append(_render_data_block(index, data))
    for index, file in enumerate(file_parts, start=1):
        sections.append(_render_file_reference(index, file))
    return "\n\n".join(sections)


def _artifact_from_output(output: str, config: dict[str, Any]) -> Artifact:
    artifacts = config.get("artifacts", {})
    if artifacts.get("parse_json_output", True):
        try:
            parsed = json.loads(output)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            max_bytes = int(artifacts.get("max_artifact_data_bytes", 65536))
            if _json_size(parsed) <= max_bytes:
                return Artifact(
                    artifactId=str(uuid.uuid4()),
                    parts=[{"kind": "data", "data": parsed}],
                    metadata={"mediaType": "application/json"},
                )
    return Artifact(
        artifactId=str(uuid.uuid4()),
        parts=[{"text": output}],
        metadata={"mediaType": "text/plain"},
    )


async def agent_card(request: web.Request) -> web.Response:
    return web.json_response(wire(build_agent_card(request.app[CONFIG_KEY])))


async def _parse_message_request(request: web.Request) -> tuple[Message, str, list[dict[str, Any]]] | web.Response:
    config = request.app[CONFIG_KEY]
    store = request.app[STORE_KEY]
    if request.content_type not in {"application/json", "application/a2a+json"}:
        return _request_error(
            request,
            "Content-Type must be application/json or application/a2a+json",
            415,
            "unsupported_content_type",
            reason="CONTENT_TYPE_NOT_SUPPORTED",
        )
    try:
        payload = await request.json()
    except (ValueError, web.HTTPException):
        return _request_error(request, "Request body must be valid JSON", 400, "malformed_json")
    try:
        if not isinstance(payload, dict) or "message" not in payload:
            raise ValueError("A message object is required")
        raw_message = payload["message"]
        if not isinstance(raw_message, dict):
            raise ValueError("A message object is required")
        raw_parts = raw_message.get("parts")
        if not isinstance(raw_parts, list) or not raw_parts:
            raise ValueError("At least one text or data part is required")
        input_file_references: list[dict[str, Any]] = []
        for index, part in enumerate(raw_parts):
            file_reference = _validate_raw_part(config, store, index, part)
            if file_reference is not None:
                input_file_references.append(file_reference)
        message = Message.model_validate(payload["message"])
        if message.role != "user":
            raise ValueError("Incoming message role must be user")
        text = _render_executor_prompt(message)
        if not text:
            raise ValueError("Message must not be empty")
        if len(text) > int(config["limits"].get("max_prompt_chars", 20000)):
            raise ValueError("Message exceeds configured prompt limit")
    except (ValidationError, ValueError, PartValidationError) as exc:
        code = getattr(exc, "code", "unsupported_message")
        return _request_error(
            request,
            f"Unsupported message: {exc}",
            400,
            code,
            reason="CONTENT_TYPE_NOT_SUPPORTED",
        )
    return message, text, input_file_references


def _persist_event(
    app: web.Application,
    task_id: str,
    event: dict[str, Any],
    *,
    terminal: bool = False,
) -> dict[str, Any]:
    event_id = app[STORE_KEY].add_task_event(task_id, event)
    envelope = {"id": event_id, "event": "message", "data": event}
    app[EVENT_BROKER_KEY].publish(task_id, envelope, terminal=terminal)
    return envelope


def _new_task(
    app: web.Application,
    message: Message,
    input_file_references: list[dict[str, Any]] | None = None,
) -> tuple[Task, dict[str, Any]]:
    store = app[STORE_KEY]
    metadata = {}
    if input_file_references:
        metadata["inputFileReferences"] = input_file_references
    task = Task(
        id=str(uuid.uuid4()), contextId=message.context_id or str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.SUBMITTED), history=[message], metadata=metadata,
    )
    store.insert_task(task, {"message": wire(message)})
    task = store.get_task(task.id)
    initial = _persist_event(app, task.id, wire(StreamResponse(task=task)))
    return task, initial


def _status_event(task: Task, *, final: bool = False) -> dict[str, Any]:
    return wire(StreamResponse(statusUpdate=TaskStatusUpdateEvent(
        taskId=task.id,
        contextId=task.context_id or "",
        status=task.status,
        metadata={"final": final},
    )))


def _artifact_event(task: Task, artifact: Artifact) -> dict[str, Any]:
    return wire(StreamResponse(artifactUpdate=TaskArtifactUpdateEvent(
        taskId=task.id,
        contextId=task.context_id or "",
        artifact=artifact,
        append=False,
        lastChunk=True,
        metadata={"final": True},
    )))


async def _run_task(app: web.Application, task: Task, text: str) -> Task:
    config, store = app[CONFIG_KEY], app[STORE_KEY]
    async with app[SEMAPHORE_KEY]:
        current = store.get_task(task.id)
        if current.status.state == TaskState.CANCELED:
            await app[EXECUTOR_MANAGER_KEY].forget(task.id)
            return current
        ownership = config.get("ownership", {})
        lease_seconds = max(1, float(ownership.get("lease_seconds", 60)))
        owner_instance_id = app[INSTANCE_ID_KEY]
        acquired = store.acquire_task_lease(
            task.id, owner_instance_id, os.getpid(), lease_seconds,
        )
        if not acquired:
            message = "Task execution failed because its ownership lease could not be acquired."
            store.update_task(
                task.id,
                TaskState.FAILED,
                error=message,
                metadata={"executor": {"status": "failed", "error": message, "leaseAcquired": False}},
                only_if_states={TaskState.SUBMITTED},
            )
            final = store.get_task(task.id)
            if final.status.state == TaskState.FAILED:
                _persist_event(app, task.id, _status_event(final, final=True), terminal=True)
            return final
        promoted = store.update_task(
            task.id,
            TaskState.WORKING,
            metadata={**current.metadata, "executor": {"status": "working"}},
            only_if_states={TaskState.SUBMITTED},
        )
        current = store.get_task(task.id)
        if not promoted:
            store.release_task_lease(task.id, owner_instance_id)
            return current
        _persist_event(app, task.id, _status_event(current))
        heartbeat = asyncio.create_task(_heartbeat_lease(app, task.id, owner_instance_id, lease_seconds))
        try:
            parameters = inspect.signature(execute).parameters
            if "task_id" in parameters and "manager" in parameters:
                output = await execute(text, config, task_id=task.id, manager=app[EXECUTOR_MANAGER_KEY])
            else:
                # Preserve compatibility with existing two-argument fake executor hooks.
                output = await execute(text, config)
            response_message = Message(
                role="agent", parts=[{"text": output}], contextId=task.context_id,
                messageId=str(uuid.uuid4()),
            )
            artifact = _artifact_from_output(output, config)
            completed = store.update_task(
                task.id,
                TaskState.COMPLETED,
                {"message": wire(response_message), "artifacts": [wire(artifact)]},
                metadata={**store.get_task(task.id).metadata, "executor": {"status": "completed", "resultText": output}},
                only_if_states={TaskState.WORKING},
            )
            if completed:
                _persist_event(app, task.id, _artifact_event(current, artifact))
        except ExecutorCanceled as exc:
            canceled = store.cancel_task(task.id)
            if canceled.status.state == TaskState.CANCELED:
                message = redact_secrets(exc, config["server"].get("auth_token"))
                store.update_task(
                    task.id,
                    TaskState.CANCELED,
                    error=message,
                    metadata={
                        **store.get_task(task.id).metadata,
                        "executor": {"status": "canceled", "error": message},
                    },
                    only_if_states={TaskState.CANCELED},
                )
        except Exception as exc:
            safe = redact_secrets(exc, config["server"].get("auth_token"))
            store.update_task(
                task.id,
                TaskState.FAILED,
                error=safe,
                metadata={**store.get_task(task.id).metadata, "executor": {"status": "failed", "error": safe}},
                only_if_states={TaskState.WORKING},
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            store.release_task_lease(task.id, owner_instance_id)
        final = store.get_task(task.id)
        if final.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
            _persist_event(app, task.id, _status_event(final, final=True), terminal=True)
        return final


async def _heartbeat_lease(
    app: web.Application, task_id: str, owner_instance_id: str, lease_seconds: float,
) -> None:
    heartbeat_interval = max(
        0.01,
        float(app[CONFIG_KEY].get("ownership", {}).get("heartbeat_interval_seconds", 10)),
    )
    cancellation_interval = max(
        0.01,
        float(app[CONFIG_KEY].get("cancellation", {}).get("poll_interval_seconds", 0.5)),
    )
    loop = asyncio.get_running_loop()
    next_heartbeat = loop.time() + heartbeat_interval
    while True:
        await asyncio.sleep(min(cancellation_interval, heartbeat_interval))
        app[STORE_KEY].expire_cancellation_requests()
        pending = app[STORE_KEY].get_pending_cancellation_for_owner(task_id, owner_instance_id)
        if pending and await _honor_cancellation_request(app, task_id, owner_instance_id, pending):
            return
        if loop.time() >= next_heartbeat:
            if not app[STORE_KEY].heartbeat_task_lease(task_id, owner_instance_id, lease_seconds):
                return
            next_heartbeat = loop.time() + heartbeat_interval


async def _honor_cancellation_request(
    app: web.Application,
    task_id: str,
    owner_instance_id: str,
    cancellation: dict[str, Any],
) -> bool:
    store = app[STORE_KEY]
    request_id = int(cancellation["id"])
    if not store.acknowledge_cancellation_request(request_id, owner_instance_id):
        return False
    grace = float(app[CONFIG_KEY].get("executor", {}).get("cancel_grace_seconds", 3))
    terminated = await app[EXECUTOR_MANAGER_KEY].cancel(task_id, grace)
    canceled = store.cancel_task(task_id)
    if canceled and canceled.status.state == TaskState.CANCELED:
        message = (
            "Task canceled after its owner acknowledged a cooperative cancellation request; "
            + ("the local executor process was terminated." if terminated else "no local subprocess handle existed.")
        )
        store.update_task(
            task_id,
            TaskState.CANCELED,
            error=message,
            metadata={
                **canceled.metadata,
                "cancellation": {
                    "cooperative": True,
                    "requestId": request_id,
                    "localProcessTerminated": terminated,
                    "message": message,
                },
            },
            only_if_states={TaskState.CANCELED},
        )
        canceled = store.get_task(task_id)
        _persist_event(app, task_id, _status_event(canceled, final=True), terminal=True)
    store.complete_cancellation_request(request_id, owner_instance_id)
    store.release_task_lease(task_id, owner_instance_id)
    return True


async def message_send(request: web.Request) -> web.Response:
    parsed = await _parse_message_request(request)
    if isinstance(parsed, web.Response):
        return parsed
    message, text, input_file_references = parsed
    task, _ = _new_task(request.app, message, input_file_references)
    result = wire(await _run_task(request.app, task, text))
    if request.headers.get("A2A-Version", "").startswith("1."):
        return web.json_response({"task": result}, content_type="application/a2a+json")
    return web.json_response(result)


def _track_background(app: web.Application, coroutine, *, task_id: str | None = None) -> asyncio.Task:
    task = asyncio.create_task(coroutine)
    app[BACKGROUND_TASKS_KEY].add(task)
    def completed(done: asyncio.Task) -> None:
        app[BACKGROUND_TASKS_KEY].discard(done)
        if task_id and not done.cancelled() and done.exception() is not None:
            app[EVENT_BROKER_KEY].close(task_id)
    task.add_done_callback(completed)
    return task


def _event_is_terminal(envelope: dict[str, Any]) -> bool:
    try:
        return TaskState(envelope["data"]["statusUpdate"]["status"]["state"]) in TERMINAL_STATES
    except (KeyError, TypeError, ValueError):
        return False


async def _stream_queue(
    request: web.Request,
    task_id: str,
    queue: asyncio.Queue | None,
    *,
    replay: list[dict[str, Any]],
    cursor: int = 0,
) -> web.StreamResponse:
    response = sse_response()
    await response.prepare(request)
    last_sent = cursor
    streaming = request.app[CONFIG_KEY].get("streaming", {})
    poll_interval = max(0.01, float(streaming.get("poll_interval_seconds", 0.5)))
    poll_limit = max(1, int(streaming.get("max_replay_events", 1000)))
    try:
        terminal = False
        for envelope in replay:
            await write_sse(response, envelope)
            last_sent = max(last_sent, int(envelope["id"]))
            terminal = terminal or _event_is_terminal(envelope)
        while queue is not None and not terminal:
            closed = False
            try:
                closed = await asyncio.wait_for(queue.get(), timeout=poll_interval) is None
            except asyncio.TimeoutError:
                pass
            stored = request.app[STORE_KEY].list_task_events(
                task_id, after_event_id=last_sent, limit=poll_limit,
            )
            for event in stored:
                envelope = event.envelope()
                await write_sse(response, envelope)
                last_sent = event.id
                if _event_is_terminal(envelope):
                    terminal = True
                    break
            if closed and not stored:
                break
        try:
            await response.write_eof()
        except ConnectionResetError:
            pass
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        if queue is not None:
            request.app[EVENT_BROKER_KEY].unsubscribe(task_id, queue)
    return response


async def message_stream(request: web.Request) -> web.StreamResponse:
    parsed = await _parse_message_request(request)
    if isinstance(parsed, web.Response):
        return parsed
    message, text, input_file_references = parsed
    broker = request.app[EVENT_BROKER_KEY]
    task, initial = _new_task(request.app, message, input_file_references)
    queue = broker.subscribe(task.id)
    _track_background(request.app, _run_task(request.app, task, text), task_id=task.id)
    return await _stream_queue(
        request,
        task.id,
        queue,
        replay=[initial],
    )


async def tasks_list(request: web.Request) -> web.Response:
    return web.json_response([wire(t) for t in request.app[STORE_KEY].list_tasks(request.query.get("status"))])


async def task_get(request: web.Request) -> web.Response:
    task = request.app[STORE_KEY].get_task(request.match_info["task_id"])
    return web.json_response(wire(task)) if task else _json_error("Task not found", 404, "task_not_found")


async def task_cancel(request: web.Request) -> web.Response:
    store = request.app[STORE_KEY]
    current = store.get_task(request.match_info["task_id"])
    if not current:
        return _json_error("Task not found", 404, "task_not_found")
    if current.status.state in TERMINAL_STATES:
        return _json_error("Task can no longer be canceled", 409, "task_not_cancelable")
    owner_instance_id = request.app[INSTANCE_ID_KEY]
    lease = store.get_task_lease(current.id)
    now = datetime.now(timezone.utc)
    lease_expired = bool(
        lease and datetime.fromisoformat(lease["lease_expires_at"]).astimezone(timezone.utc) <= now
    )
    if lease and not lease_expired and lease["owner_instance_id"] != owner_instance_id:
        cancellation = request.app[CONFIG_KEY].get("cancellation", {})
        created = store.create_cancellation_request(
            current.id,
            owner_instance_id,
            lease["owner_instance_id"],
            max(1, float(cancellation.get("request_ttl_seconds", 300))),
            reason="Cancellation requested through the task API.",
        )
        return web.json_response(
            {
                "success": False,
                "code": "cancellation_requested",
                "error": "Task is owned by another active server instance. A cooperative cancellation request was recorded.",
                "task_id": current.id,
                "owner_instance_id": lease["owner_instance_id"],
                "request_id": created["id"],
            },
            status=409,
        )
    if lease_expired:
        store.acquire_task_lease(
            current.id,
            owner_instance_id,
            os.getpid(),
            max(1, float(request.app[CONFIG_KEY].get("ownership", {}).get("lease_seconds", 60))),
        )
        lease = store.get_task_lease(current.id)
    canceled = store.cancel_task(request.match_info["task_id"])
    if canceled.status.state != TaskState.CANCELED:
        return _json_error("Task can no longer be canceled", 409, "task_not_cancelable")
    grace = float(request.app[CONFIG_KEY].get("executor", {}).get("cancel_grace_seconds", 3))
    terminated = await request.app[EXECUTOR_MANAGER_KEY].cancel(canceled.id, grace)
    if terminated:
        message = "Task canceled and the local executor process was terminated."
    elif current.status.state == TaskState.SUBMITTED:
        message = "Task canceled before executor startup."
    elif lease and lease.get("owner_instance_id") == owner_instance_id:
        message = "Task canceled; this server owned the lease but no local subprocess handle existed."
    else:
        message = "Cancellation requested, but this server process does not own the executor process."
    store.release_task_lease(canceled.id, owner_instance_id)
    store.update_task(
        canceled.id,
        TaskState.CANCELED,
        error=message,
        metadata={
            **canceled.metadata,
            "cancellation": {"localProcessTerminated": terminated, "message": message},
        },
        only_if_states={TaskState.CANCELED},
    )
    canceled = store.get_task(canceled.id)
    _persist_event(request.app, canceled.id, _status_event(canceled, final=True), terminal=True)
    return web.json_response(wire(canceled))


async def task_subscribe(request: web.Request) -> web.StreamResponse:
    store, broker = request.app[STORE_KEY], request.app[EVENT_BROKER_KEY]
    task_id = request.match_info["task_id"]
    raw_last_id = request.headers.get("Last-Event-ID")
    if raw_last_id is not None:
        try:
            last_event_id = int(raw_last_id)
            if last_event_id < 0:
                raise ValueError
        except ValueError:
            return _json_error("Last-Event-ID must be a non-negative integer", 400, "invalid_last_event_id")
    else:
        last_event_id = None
    current = store.get_task(task_id)
    if not current:
        return _json_error("Task not found", 404, "task_not_found")
    bounds = store.get_event_bounds(task_id)
    oldest_event_id = bounds["oldest_event_id"]
    if (
        last_event_id is not None
        and oldest_event_id is not None
        and last_event_id < oldest_event_id - 1
    ):
        streaming = request.app[CONFIG_KEY].get("streaming", {})
        return web.json_response(
            {
                "success": False,
                "error": "Requested replay cursor is no longer available because event history was pruned.",
                "code": streaming.get("replay_gap_error_code", "replay_gap"),
                "task_id": task_id,
                "last_event_id": last_event_id,
                "oldest_available_event_id": oldest_event_id,
            },
            status=int(streaming.get("replay_gap_status_code", 409)),
        )
    queue = None if current.status.state in TERMINAL_STATES else broker.subscribe(task_id)
    replay_limit = max(1, int(request.app[CONFIG_KEY].get("streaming", {}).get("max_replay_events", 1000)))
    stored = store.list_task_events(task_id, after_event_id=last_event_id, limit=replay_limit)
    replay = [event.envelope() for event in stored]
    if not replay and last_event_id is None and current.status.state not in TERMINAL_STATES:
        replay = [_persist_event(request.app, task_id, wire(StreamResponse(task=current)))]
    current = store.get_task(task_id)
    if current.status.state in TERMINAL_STATES and queue is not None:
        broker.unsubscribe(task_id, queue)
        queue = None
        stored = store.list_task_events(task_id, after_event_id=last_event_id, limit=replay_limit)
        replay = [event.envelope() for event in stored]
    if current.status.state in TERMINAL_STATES and not replay:
        return _json_error("No newer stored events are available", 409, "no_new_events")
    return await _stream_queue(
        request,
        task_id,
        queue,
        replay=replay,
        cursor=last_event_id or 0,
    )


async def _startup_maintenance(app: web.Application) -> None:
    config, store = app[CONFIG_KEY], app[STORE_KEY]
    if config.get("retention", {}).get("prune_on_startup", True):
        prune_events(store, config)
    expire_cancellations(store)
    if config.get("ownership", {}).get("recover_expired_leases_on_startup", True):
        recover_expired_leases(store, config)
    if config.get("recovery", {}).get("recover_on_startup", True):
        recover_stale(store, config, include_expired_leases=False)


async def _cleanup_background(app: web.Application) -> None:
    await app[EXECUTOR_MANAGER_KEY].cancel_all(0)
    tasks = tuple(app[BACKGROUND_TASKS_KEY])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(config: dict[str, Any], store: Store | None = None) -> web.Application:
    app = web.Application(middlewares=[json_error_middleware, auth_middleware])
    app[CONFIG_KEY] = config
    if store is None:
        store = Store(database_path(), config.get("sqlite", {}), config.get("faults", {}))
    else:
        store.configure_sqlite(config.get("sqlite", {}), config.get("faults", {}))
    app[STORE_KEY] = store
    app[SEMAPHORE_KEY] = asyncio.Semaphore(int(config.get("limits", {}).get("max_concurrent_tasks", 1)))
    app[EVENT_BROKER_KEY] = EventBroker()
    app[BACKGROUND_TASKS_KEY] = set()
    app[EXECUTOR_MANAGER_KEY] = ExecutorManager()
    app[INSTANCE_ID_KEY] = str(uuid.uuid4())
    app.on_startup.append(_startup_maintenance)
    app.on_cleanup.append(_cleanup_background)
    app.router.add_get("/health", health)
    app.router.add_get("/.well-known/agent-card.json", agent_card)
    app.router.add_get("/files/{file_id}/metadata", file_metadata_get)
    app.router.add_get("/files/{file_id}", file_bytes_get)
    app.router.add_post("/message:send", message_send)
    app.router.add_post("/message:stream", message_stream)
    app.router.add_get("/tasks", tasks_list)
    app.router.add_get("/tasks/{task_id}", task_get)
    app.router.add_post("/tasks/{task_id}:cancel", task_cancel)
    app.router.add_post("/tasks/{task_id}:subscribe", task_subscribe)
    return app


def serve(config: dict[str, Any], host: str | None = None, port: int | None = None) -> None:
    host = host or config["server"]["host"]
    port = port or int(config["server"]["port"])
    validate_server_bind(config, host)
    web.run_app(create_app(config), host=host, port=port, print=None)
