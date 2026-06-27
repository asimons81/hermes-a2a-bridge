"""Hermes tool handlers. Every public handler returns JSON and never raises."""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from . import client
from .auth import redact_secrets
from .config import database_path, load_config
from .diagnostics import diagnose_peer
from .errors import ClientError
from .store import Store

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _store() -> Store:
    config = load_config()
    return Store(database_path(), config.get("sqlite", {}), config.get("faults", {}))


def _resolve(value: str, explicit_token: str | None = None) -> tuple[str, str | None]:
    if urlparse(value).scheme in {"http", "https"}:
        return value, explicit_token
    entry = _store().registry_get(value)
    if not entry:
        raise ValueError(f"Unknown agent URL or registry name: {value}")
    return entry["url"], explicit_token if explicit_token is not None else entry["token"]


async def _endpoint(value: str, token: str | None) -> tuple[str, str | None]:
    base, token = _resolve(value, token)
    card = await client.fetch_agent_card(base)
    return client.agent_endpoint(card), token


def safe_handler(fn: Callable[..., Awaitable[Any]]):
    async def wrapped(args: dict, **kwargs) -> str:
        args = args if isinstance(args, dict) else {}
        known = [args.get("token")]
        try:
            agent_ref = args.get("agent_url")
            if agent_ref and urlparse(agent_ref).scheme not in {"http", "https"}:
                entry = _store().registry_get(agent_ref)
                if entry:
                    known.append(entry.get("token"))
            return _result({"success": True, **(await fn(args, **kwargs))})
        except ClientError as exc:
            if isinstance(exc.payload, dict):
                return _result(_redact_tool_payload(exc.payload, *known))
            payload = {"success": False, "error": redact_secrets(exc, *known)}
            if exc.code:
                payload["code"] = exc.code
            return _result(payload)
        except Exception as exc:
            return _result({"success": False, "error": redact_secrets(exc, *known)})
    wrapped.__name__ = fn.__name__
    return wrapped


def _redact_tool_payload(payload: dict[str, Any], *known: str | None) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload))
    redacted = _redact_value(redacted, *known)
    if isinstance(redacted, dict):
        redacted.setdefault("success", False)
    return redacted


def _redact_value(value: Any, *known: str | None) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value(item, *known) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, *known) for item in value]
    if isinstance(value, str):
        return redact_secrets(value, *known)
    return value


def _tool_file_ids(args: dict[str, Any]) -> list[str] | None:
    if "file_ids" not in args or args.get("file_ids") in (None, []):
        return None
    file_ids = args["file_ids"]
    if not isinstance(file_ids, list):
        raise ClientError(
            "file_ids must be an array of stored Hermes file IDs.",
            payload={
                "success": False,
                "error": "file_ids must be an array of stored Hermes file IDs.",
                "code": "invalid_file_ids",
            },
        )
    return client.validate_file_ids(file_ids)


@safe_handler
async def a2a_discover_agent(args: dict, **kwargs):
    return {"agent": await client.fetch_agent_card(args["url"])}


@safe_handler
async def a2a_doctor_peer(args: dict, **kwargs):
    base, token = _resolve(args["agent_url"], args.get("token"))
    return _redact_value(
        await diagnose_peer(
            base,
            token=token,
            timeout_seconds=args.get("timeout_seconds"),
            live_probe=bool(args.get("live_probe", False)),
            stream_probe=bool(args.get("stream_probe", False)),
            stream_probe_timeout=args.get("stream_probe_timeout"),
            stream_probe_max_events=args.get("stream_probe_max_events"),
            probe_message=args.get("probe_message"),
        ),
        token,
    )


@safe_handler
async def a2a_send_message(args: dict, **kwargs):
    file_ids = _tool_file_ids(args)
    base, token = await _endpoint(args["agent_url"], args.get("token"))
    if not args.get("message") and "data" not in args:
        raise ValueError("message or data is required")
    extra = {"data": args["data"]} if "data" in args else {}
    if file_ids:
        extra["file_ids"] = file_ids
    task = await client.send_message(
        base,
        args.get("message"),
        token,
        args.get("context_id"),
        args.get("timeout_seconds"),
        **extra,
    )
    return {"task": task, "resultText": _task_text(task)}


@safe_handler
async def a2a_get_task(args: dict, **kwargs):
    base, token = await _endpoint(args["agent_url"], args.get("token"))
    task = await client.get_task(base, args["task_id"], token)
    return {"task": task, "resultText": _task_text(task)}


@safe_handler
async def a2a_list_tasks(args: dict, **kwargs):
    base, token = await _endpoint(args["agent_url"], args.get("token"))
    return {"tasks": await client.list_tasks(base, token, args.get("status"))}


@safe_handler
async def a2a_cancel_task(args: dict, **kwargs):
    base, token = await _endpoint(args["agent_url"], args.get("token"))
    task = await client.cancel_task(base, args["task_id"], token)
    return {"task": task, "resultText": _task_text(task)}


@safe_handler
async def a2a_registry_add(args: dict, **kwargs):
    if not NAME_RE.match(args["name"]):
        raise ValueError("Registry names must start with a letter or digit and use only letters, digits, dot, underscore, or hyphen")
    client.require_http_url(args["url"])
    _store().registry_add(args["name"], args["url"], args.get("token"))
    return {"agent": {"name": args["name"], "url": args["url"], "hasToken": bool(args.get("token"))}}


@safe_handler
async def a2a_registry_list(args: dict, **kwargs):
    return {"agents": _store().registry_list()}


@safe_handler
async def a2a_registry_remove(args: dict, **kwargs):
    return {"agent": {"name": args["name"], "removed": _store().registry_remove(args["name"])}}


def _task_text(task: dict[str, Any]) -> str | None:
    try:
        parts = task["status"]["message"]["parts"]
    except (KeyError, TypeError):
        return None
    texts = [part.get("text") for part in parts if isinstance(part, dict) and part.get("text")]
    return "\n".join(texts) if texts else None


HANDLERS = {
    fn.__name__: fn for fn in (
        a2a_discover_agent, a2a_doctor_peer, a2a_send_message, a2a_get_task, a2a_list_tasks,
        a2a_cancel_task, a2a_registry_add, a2a_registry_list, a2a_registry_remove,
    )
}
