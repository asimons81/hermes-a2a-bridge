"""Precise Hermes tool schemas."""

from __future__ import annotations


def _schema(description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"name": "", "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required or [], "additionalProperties": False,
    }}


URL = {"type": "string", "description": "HTTP(S) agent base URL, Agent Card URL, or registry name."}
TOKEN = {"type": "string", "description": "Optional bearer token. Never returned in output."}
NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 64,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    "description": "Short registry name using letters, digits, dot, underscore, or hyphen.",
}

TOOL_SCHEMAS = {
    "a2a_discover_agent": _schema("Fetch an A2A Agent Card.", {"url": URL}, ["url"]),
    "a2a_doctor_peer": _schema(
        "Safely diagnose A2A peer compatibility from Agent Card metadata only.",
        {
            "agent_url": URL,
            "token": TOKEN,
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
        },
        ["agent_url"],
    ),
    "a2a_send_message": _schema("Send one text and/or structured JSON data message to an A2A agent.", {
        "agent_url": URL,
        "message": {"type": "string", "minLength": 1},
        "data": {
            "anyOf": [{"type": "object"}, {"type": "array"}],
            "description": "Optional structured JSON object or array to send as a data part.",
        },
        "file_ids": {
            "type": "array",
            "items": {
                "type": "string",
                "pattern": "^file_[A-Za-z0-9_-]{16,}$",
            },
            "description": "Optional stored Hermes file ID references only. Does not accept paths, URLs, or bytes.",
        },
        "token": TOKEN,
        "context_id": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
    }, ["agent_url"]),
    "a2a_get_task": _schema("Get one remote A2A task.", {
        "agent_url": URL, "task_id": {"type": "string"}, "token": TOKEN,
    }, ["agent_url", "task_id"]),
    "a2a_list_tasks": _schema("List remote A2A tasks.", {
        "agent_url": URL, "token": TOKEN, "status": {"type": "string"},
    }, ["agent_url"]),
    "a2a_cancel_task": _schema("Cancel a remote A2A task.", {
        "agent_url": URL, "task_id": {"type": "string"}, "token": TOKEN,
    }, ["agent_url", "task_id"]),
    "a2a_registry_add": _schema("Add or replace a named remote agent.", {
        "name": NAME, "url": URL, "token": TOKEN,
    }, ["name", "url"]),
    "a2a_registry_list": _schema("List named remote agents without exposing tokens.", {}),
    "a2a_registry_remove": _schema("Remove a named remote agent.", {"name": NAME}, ["name"]),
}

for _name, _value in TOOL_SCHEMAS.items():
    _value["name"] = _name
