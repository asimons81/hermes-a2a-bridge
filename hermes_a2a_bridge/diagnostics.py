"""Safe peer compatibility diagnostics based on Agent Card metadata by default."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .auth import redact_secrets
from . import client
from .client import DEFAULT_TIMEOUT_SECONDS, require_http_url
from .errors import ClientError


CAPABILITY_DEFAULTS = {
    "message_send": False,
    "message_stream": False,
    "tasks_get": False,
    "tasks_cancel": False,
    "tasks_subscribe": False,
    "file_references": False,
}
DEFAULT_LIVE_PROBE_MESSAGE = "Hermes A2A Bridge diagnostic ping. No action required."
DEFAULT_STREAM_PROBE_MESSAGE = "Hermes A2A Bridge streaming diagnostic ping. No action required."
DEFAULT_STREAM_PROBE_TIMEOUT_SECONDS = 10
DEFAULT_STREAM_PROBE_MAX_EVENTS = 20
TERMINAL_STATE_VALUES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


def agent_card_url(url: str) -> str:
    target = require_http_url(url)
    path = urlparse(target).path
    if path.endswith("/.well-known/agent-card.json"):
        return target
    return f"{target}/.well-known/agent-card.json"


def _empty_result(url: str, card_url: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "unknown",
        "url": url,
        "agent_card_url": card_url,
        "name": None,
        "protocol": {"binding": None, "version": None},
        "capabilities": dict(CAPABILITY_DEFAULTS),
        "warnings": [],
        "errors": [],
        "recommendations": [],
        "live_probe": {"enabled": False, "attempted": False},
        "stream_probe": {"enabled": False, "attempted": False},
    }


def _protocol_major(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.split(".", 1)[0]


def _interface_binding(interface: dict[str, Any]) -> str | None:
    value = interface.get("protocolBinding") or interface.get("transport")
    return str(value).upper() if value else None


def _is_http_json(interface: dict[str, Any]) -> bool:
    binding = _interface_binding(interface)
    return binding in {None, "HTTP+JSON", "HTTP_JSON"}


def _interfaces(card: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for field in ("supportedInterfaces", "additionalInterfaces"):
        value = card.get(field)
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, dict))
    if isinstance(card.get("url"), str) and card["url"]:
        found.append({
            "url": card["url"],
            "protocolBinding": card.get("preferredTransport") or "HTTP+JSON",
            "protocolVersion": card.get("protocolVersion") or "1.0",
        })
    return found


def _best_http_json_interface(card: dict[str, Any]) -> dict[str, Any] | None:
    for interface in _interfaces(card):
        if not interface.get("url") or not _is_http_json(interface):
            continue
        version = interface.get("protocolVersion") or card.get("protocolVersion") or "1.0"
        if _protocol_major(version) == "1":
            return interface
    return None


def _versions(card: dict[str, Any]) -> list[str]:
    versions: list[str] = []
    if isinstance(card.get("protocolVersion"), str):
        versions.append(card["protocolVersion"])
    for interface in _interfaces(card):
        if isinstance(interface.get("protocolVersion"), str):
            versions.append(interface["protocolVersion"])
    return versions


def _json_rpc_only(card: dict[str, Any]) -> bool:
    if str(card.get("preferredTransport", "")).upper() == "JSONRPC":
        interfaces = _interfaces(card)
        return not any(_is_http_json(interface) and interface.get("url") for interface in interfaces)
    interfaces = _interfaces(card)
    return bool(interfaces) and all(_interface_binding(interface) == "JSONRPC" for interface in interfaces)


def _auth_likely_required(card: dict[str, Any]) -> bool:
    if card.get("securitySchemes") or card.get("securityRequirements"):
        return True
    for skill in card.get("skills", []) if isinstance(card.get("skills"), list) else []:
        if isinstance(skill, dict) and skill.get("securityRequirements"):
            return True
    return False


def _file_reference_status(card: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    hermes = metadata.get("hermesA2ABridge") if isinstance(metadata, dict) else None
    refs = hermes.get("fileReferences") if isinstance(hermes, dict) else None
    warnings: list[str] = []
    recommendations: list[str] = []
    if isinstance(refs, dict) and refs.get("supported") is True:
        scope = refs.get("scope")
        accepted = refs.get("acceptedShapes")
        if scope == "pre_staged_local_file_id_references_only" or "fileId" in json.dumps(accepted or {}):
            return True, warnings, recommendations
        warnings.append("Peer advertises file references, but not Hermes stored file ID references.")
        recommendations.append("Use text or JSON data parts, or confirm the peer accepts {file:{fileId}} metadata only.")
        return False, warnings, recommendations

    text = json.dumps(card, sort_keys=True)
    if any(marker in text for marker in ("fileReferences", "filePart", "fileId", "inline_bytes", "uri")):
        warnings.append("Peer includes file-reference metadata that Hermes cannot prove compatible.")
        recommendations.append("Do not send file references unless the peer documents Hermes stored file ID support.")
    return False, warnings, recommendations


def analyze_agent_card(url: str, card_url: str, card: dict[str, Any]) -> dict[str, Any]:
    result = _empty_result(url, card_url)
    result["ok"] = True
    result["name"] = card.get("name")
    warnings = result["warnings"]
    errors = result["errors"]
    recommendations = result["recommendations"]

    endpoint = _best_http_json_interface(card)
    json_rpc_only = _json_rpc_only(card)
    versions = _versions(card)
    zero_versions = [version for version in versions if _protocol_major(version) == "0"]
    capabilities = dict(CAPABILITY_DEFAULTS)

    if endpoint:
        version = endpoint.get("protocolVersion") or card.get("protocolVersion") or "1.0"
        binding = endpoint.get("protocolBinding") or endpoint.get("transport") or "HTTP+JSON"
        result["protocol"] = {"binding": binding, "version": version}
        capabilities["message_send"] = True
        capabilities["tasks_get"] = True
        capabilities["tasks_cancel"] = True
        streaming = bool((card.get("capabilities") or {}).get("streaming"))
        capabilities["message_stream"] = streaming
        capabilities["tasks_subscribe"] = streaming
        if not streaming:
            warnings.append("Peer does not advertise streaming support.")
            recommendations.append("Use send/task lookup flows instead of stream or subscribe.")
    elif json_rpc_only:
        result["protocol"] = {"binding": "JSONRPC", "version": versions[0] if versions else None}
        errors.append("Peer appears to advertise JSON-RPC only, which Hermes A2A Bridge does not implement.")
        recommendations.append("Use a peer that advertises an HTTP+JSON 1.x interface.")
    elif zero_versions:
        result["protocol"] = {"binding": None, "version": zero_versions[0]}
        errors.append("Peer appears to advertise A2A 0.3 only, which Hermes A2A Bridge does not implement.")
        recommendations.append("Use an A2A 1.x HTTP+JSON peer; Hermes does not implement /v1 or 0.3 envelopes.")
    else:
        errors.append("Agent Card does not advertise a usable HTTP+JSON 1.x endpoint.")
        recommendations.append("Ask the peer operator for a supportedInterfaces HTTP+JSON protocolVersion 1.x endpoint.")

    if zero_versions and endpoint:
        warnings.append("Peer also advertises older A2A 0.x metadata; Hermes will use only HTTP+JSON 1.x.")
    if _auth_likely_required(card):
        warnings.append("Agent Card advertises authentication requirements.")
        recommendations.append("Provide the correct bearer token through the registry or command option before sending messages.")

    file_ok, file_warnings, file_recommendations = _file_reference_status(card)
    capabilities["file_references"] = file_ok
    warnings.extend(file_warnings)
    recommendations.extend(file_recommendations)
    result["capabilities"] = capabilities

    if errors:
        result["status"] = "unsupported"
    elif capabilities["message_send"] and not capabilities["message_stream"]:
        result["status"] = "partially_compatible"
    elif capabilities["message_send"]:
        result["status"] = "compatible"
    else:
        result["status"] = "unknown"
    return result


def _set_probe_skipped(result: dict[str, Any], enabled: bool, reason: str) -> dict[str, Any]:
    if enabled:
        result["live_probe"] = {
            "enabled": True,
            "attempted": False,
            "status": "skipped",
            "reason": reason,
        }
    return result


def _set_stream_probe_skipped(result: dict[str, Any], enabled: bool, reason: str) -> dict[str, Any]:
    if enabled:
        result["stream_probe"] = {
            "enabled": True,
            "attempted": False,
            "status": "skipped",
            "reason": reason,
        }
    return result


def _task_id(task: Any) -> str | None:
    if not isinstance(task, dict):
        return None
    task_id = task.get("id")
    return task_id if isinstance(task_id, str) and task_id else None


def _task_status(task: Any) -> str | None:
    if not isinstance(task, dict):
        return None
    status = task.get("status")
    if isinstance(status, dict) and isinstance(status.get("state"), str):
        return status["state"]
    return None


def _event_type(envelope: dict[str, Any]) -> str:
    data = envelope.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("task"), dict):
            return "task"
        if isinstance(data.get("statusUpdate"), dict):
            return "statusUpdate"
        if isinstance(data.get("artifactUpdate"), dict):
            return "artifactUpdate"
    return str(envelope.get("event") or "message")


def _event_task_id(envelope: dict[str, Any]) -> str | None:
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    task = data.get("task")
    if isinstance(task, dict) and isinstance(task.get("id"), str):
        return task["id"]
    for key in ("statusUpdate", "artifactUpdate"):
        update = data.get(key)
        if isinstance(update, dict) and isinstance(update.get("taskId"), str):
            return update["taskId"]
    return None


def _event_status(envelope: dict[str, Any]) -> str | None:
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    task = data.get("task")
    if isinstance(task, dict):
        return _task_status(task)
    update = data.get("statusUpdate")
    if isinstance(update, dict):
        status = update.get("status")
        if isinstance(status, dict) and isinstance(status.get("state"), str):
            return status["state"]
    return None


def _error_payload(message: str, exc: Exception, token: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": redact_secrets(message, token)}
    if isinstance(exc, ClientError):
        if exc.status is not None:
            payload["http_status"] = exc.status
        if isinstance(exc.payload, dict):
            payload["details"] = _redact_value(exc.payload, token)
    return payload


async def _run_live_probe(
    endpoint: str,
    *,
    token: str | None,
    timeout_seconds: int | None,
    probe_message: str,
    session: aiohttp.ClientSession,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "enabled": True,
        "attempted": True,
        "message_send": False,
        "task_id": None,
        "task_status": None,
        "task_get": None,
        "status": "failed",
        "warnings": [],
        "errors": [],
    }
    try:
        task = await client.send_message(
            endpoint,
            probe_message,
            token,
            timeout_seconds=timeout_seconds,
            session=session,
        )
    except Exception as exc:
        probe["errors"].append(_error_payload(f"Diagnostic message send failed: {exc}", exc, token))
        return probe

    probe["message_send"] = True
    probe["task_id"] = _task_id(task)
    probe["task_status"] = _task_status(task)
    if not probe["task_id"]:
        probe["status"] = "passed"
        return probe

    try:
        looked_up = await client.get_task(endpoint, probe["task_id"], token, session=session)
        probe["task_get"] = True
        probe["task_status"] = _task_status(looked_up) or probe["task_status"]
        probe["status"] = "passed"
    except Exception as exc:
        probe["task_get"] = False
        probe["status"] = "passed_with_warnings"
        probe["warnings"].append(_error_payload(f"Task lookup failed: {exc}", exc, token))
    return probe


async def _run_stream_probe(
    endpoint: str,
    *,
    token: str | None,
    probe_message: str,
    timeout_seconds: int,
    max_events: int,
    session: aiohttp.ClientSession,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "enabled": True,
        "attempted": True,
        "status": "failed",
        "message_stream": False,
        "events_received": 0,
        "event_types": [],
        "task_id": None,
        "terminal_observed": False,
        "warnings": [],
        "errors": [],
    }
    stream = client.stream_message(
        endpoint,
        probe_message,
        token,
        session=session,
    )
    started = time.monotonic()
    try:
        while probe["events_received"] < max_events:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                probe["warnings"].append("Stream probe stopped after timeout.")
                break
            try:
                event = await asyncio.wait_for(anext(stream), timeout=remaining)
            except StopAsyncIteration:
                probe["message_stream"] = True
                break
            probe["message_stream"] = True
            probe["events_received"] += 1
            probe["event_types"].append(_event_type(event))
            probe["task_id"] = probe["task_id"] or _event_task_id(event)
            status = _event_status(event)
            if status in TERMINAL_STATE_VALUES:
                probe["terminal_observed"] = True
                break
        if probe["events_received"] >= max_events and not probe["terminal_observed"]:
            probe["warnings"].append("Stream probe stopped after max events.")
        if probe["message_stream"] and not probe["events_received"]:
            probe["warnings"].append("Stream probe received no events.")
        probe["status"] = "passed" if not probe["warnings"] else "passed_with_warnings"
    except asyncio.TimeoutError:
        if probe["events_received"]:
            probe["warnings"].append("Stream probe stopped after timeout.")
            probe["status"] = "passed_with_warnings"
        else:
            probe["errors"].append({"message": "Stream probe timed out before receiving events."})
    except Exception as exc:
        probe["errors"].append(_error_payload(f"Diagnostic stream failed: {exc}", exc, token))
    finally:
        await stream.aclose()
    return probe


async def diagnose_peer(
    url: str,
    *,
    token: str | None = None,
    timeout_seconds: int | None = None,
    live_probe: bool = False,
    stream_probe: bool = False,
    stream_probe_timeout: int | None = None,
    stream_probe_max_events: int | None = None,
    probe_message: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    base_url = require_http_url(url)
    card_url = agent_card_url(base_url)
    result = _empty_result(base_url, card_url)
    result["live_probe"] = {"enabled": bool(live_probe), "attempted": False}
    result["stream_probe"] = {"enabled": bool(stream_probe), "attempted": False}
    own = session is None
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
    session = session or aiohttp.ClientSession(timeout=timeout)
    try:
        headers = {"Accept": "application/a2a+json, application/json", "A2A-Version": "1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with session.get(card_url, headers=headers) as response:
            body = await response.text()
            if response.status in {401, 403}:
                result["errors"].append(f"Agent Card requires authentication (HTTP {response.status}).")
                result["recommendations"].append("Provide a valid bearer token or save one in the registry entry.")
                return _set_stream_probe_skipped(
                    _set_probe_skipped(result, live_probe, "agent_card_unavailable"),
                    stream_probe,
                    "live_probe_required" if stream_probe and not live_probe else "agent_card_unavailable",
                )
            if response.status == 404:
                result["errors"].append("Agent Card was not found at the well-known URL.")
                result["recommendations"].append("Check the base URL or pass a direct Agent Card URL.")
                return _set_stream_probe_skipped(
                    _set_probe_skipped(result, live_probe, "agent_card_unavailable"),
                    stream_probe,
                    "live_probe_required" if stream_probe and not live_probe else "agent_card_unavailable",
                )
            if response.status >= 400:
                result["errors"].append(f"Agent Card request failed with HTTP {response.status}.")
                result["recommendations"].append("Check peer availability and whether discovery is public.")
                return _set_stream_probe_skipped(
                    _set_probe_skipped(result, live_probe, "agent_card_unavailable"),
                    stream_probe,
                    "live_probe_required" if stream_probe and not live_probe else "agent_card_unavailable",
                )
        try:
            card = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            result["errors"].append("Agent Card response was not valid JSON.")
            result["recommendations"].append("Ask the peer operator to serve valid JSON at the Agent Card URL.")
            return _set_stream_probe_skipped(
                _set_probe_skipped(result, live_probe, "invalid_agent_card"),
                stream_probe,
                "live_probe_required" if stream_probe and not live_probe else "invalid_agent_card",
            )
        card = _redact_value(card, token)
        if not isinstance(card, dict):
            result["errors"].append("Agent Card JSON was not an object.")
            return _set_stream_probe_skipped(
                _set_probe_skipped(result, live_probe, "invalid_agent_card"),
                stream_probe,
                "live_probe_required" if stream_probe and not live_probe else "invalid_agent_card",
            )
        if not card.get("name"):
            result["errors"].append("Agent Card is missing required name metadata.")
        if not _interfaces(card):
            result["errors"].append("Agent Card does not declare an endpoint.")
        if result["errors"]:
            result["ok"] = True
            return _set_stream_probe_skipped(
                _set_probe_skipped(result, live_probe, "invalid_agent_card"),
                stream_probe,
                "live_probe_required" if stream_probe and not live_probe else "invalid_agent_card",
            )
        result = analyze_agent_card(base_url, card_url, card)
        result["live_probe"] = {"enabled": bool(live_probe), "attempted": False}
        result["stream_probe"] = {"enabled": bool(stream_probe), "attempted": False}
        if stream_probe and not live_probe:
            _set_stream_probe_skipped(result, True, "live_probe_required")
        if not live_probe:
            return result
        if result.get("status") not in {"compatible", "partially_compatible"}:
            return _set_stream_probe_skipped(
                _set_probe_skipped(result, live_probe, "metadata_unsupported"),
                stream_probe,
                "metadata_unsupported",
            )
        endpoint = _best_http_json_interface(card)
        if not endpoint or not endpoint.get("url"):
            return _set_stream_probe_skipped(
                _set_probe_skipped(result, live_probe, "metadata_unsupported"),
                stream_probe,
                "metadata_unsupported",
            )
        result["live_probe"] = await _run_live_probe(
            str(endpoint["url"]),
            token=token,
            timeout_seconds=timeout_seconds,
            probe_message=probe_message or DEFAULT_LIVE_PROBE_MESSAGE,
            session=session,
        )
        if not stream_probe:
            return result
        if not result.get("capabilities", {}).get("message_stream"):
            return _set_stream_probe_skipped(result, True, "metadata_streaming_unsupported")
        result["stream_probe"] = await _run_stream_probe(
            str(endpoint["url"]),
            token=token,
            probe_message=probe_message or DEFAULT_STREAM_PROBE_MESSAGE,
            timeout_seconds=max(1, stream_probe_timeout or DEFAULT_STREAM_PROBE_TIMEOUT_SECONDS),
            max_events=max(1, stream_probe_max_events or DEFAULT_STREAM_PROBE_MAX_EVENTS),
            session=session,
        )
        return result
    except asyncio.TimeoutError:
        result["errors"].append("Agent Card request timed out.")
        result["recommendations"].append("Retry with a larger --timeout or check peer availability.")
        return _set_stream_probe_skipped(
            _set_probe_skipped(result, live_probe, "agent_card_unavailable"),
            stream_probe,
            "live_probe_required" if stream_probe and not live_probe else "agent_card_unavailable",
        )
    except aiohttp.ClientError as exc:
        result["errors"].append(redact_secrets(f"Agent Card request failed: {exc}", token))
        result["recommendations"].append("Check the peer URL and network path.")
        return _set_stream_probe_skipped(
            _set_probe_skipped(result, live_probe, "agent_card_unavailable"),
            stream_probe,
            "live_probe_required" if stream_probe and not live_probe else "agent_card_unavailable",
        )
    finally:
        if own:
            await session.close()


def _redact_value(value: Any, *known: str | None) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value(item, *known) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, *known) for item in value]
    if isinstance(value, str):
        return redact_secrets(value, *known)
    return value
