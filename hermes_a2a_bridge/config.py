"""Configuration creation and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .auth import generate_token
from .errors import ConfigError
from .executor import VERIFIED_HERMES_COMMAND


def a2a_home() -> Path:
    override = os.environ.get("HERMES_A2A_HOME")
    return Path(override).expanduser() if override else Path.home() / ".hermes" / "a2a"


def config_path() -> Path:
    return a2a_home() / "config.yaml"


def database_path() -> Path:
    return a2a_home() / "tasks.sqlite3"


def default_config() -> dict[str, Any]:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 8765,
            "public_url": "http://127.0.0.1:8765",
            "public_url_explicit": False,
            "require_auth": True,
            "auth_token": generate_token(),
            "allow_remote_hosts": False,
        },
        "agent_card": {
            "name": "Hermes Agent",
            "description": "A local Hermes Agent exposed through the Agent-to-Agent protocol.",
            "version": "0.4.6",
            "provider": {"organization": "local", "url": "http://127.0.0.1:8765"},
            "default_input_modes": ["text/plain", "application/json"],
            "default_output_modes": ["text/plain", "application/json"],
            "skills": [{
                "id": "hermes-chat",
                "name": "Hermes Chat",
                "description": "Send a text task to the local Hermes Agent.",
                "tags": ["hermes", "local-agent", "text"],
            }],
        },
        "executor": {
            "command": list(VERIFIED_HERMES_COMMAND),
            "timeout_seconds": 300,
            "cancel_grace_seconds": 3,
            "max_prompt_chars": 20000,
        },
        "limits": {"max_prompt_chars": 20000, "max_concurrent_tasks": 1, "task_timeout_seconds": 300},
        "parts": {
            "max_data_part_bytes": 65536,
            "allow_data_parts": True,
            "allow_file_parts": False,
            "allow_file_id_references": False,
            "allow_remote_url_file_references": False,
            "allow_inline_file_bytes": False,
        },
        "files": {
            "storage_dir": "~/.hermes/a2a/files",
            "max_file_bytes": 10485760,
            "max_total_storage_bytes": 1073741824,
            "allowed_mime_types": [
                "text/plain",
                "application/json",
                "text/markdown",
                "text/csv",
                "application/pdf",
                "image/png",
                "image/jpeg",
            ],
            "reject_unknown_mime": True,
            "allow_remote_url_references": True,
            "auto_fetch_remote_urls": False,
            "allow_inline_bytes": False,
            "max_inline_bytes": 0,
            "cleanup_deleted_task_files": False,
            "shard_depth": 2,
        },
        "artifacts": {
            "parse_json_output": True,
            "max_artifact_data_bytes": 65536,
        },
        "retention": {
            "max_events_per_task": 500,
            "max_event_age_days": 30,
            "prune_on_startup": True,
        },
        "recovery": {
            "stale_task_after_seconds": 900,
            "recover_on_startup": True,
            "stale_working_state": "TASK_STATE_FAILED",
        },
        "ownership": {
            "lease_seconds": 60,
            "heartbeat_interval_seconds": 10,
            "recover_expired_leases_on_startup": True,
            "expired_lease_state": "TASK_STATE_FAILED",
        },
        "sqlite": {
            "busy_timeout_ms": 5000,
            "journal_mode": "WAL",
            "synchronous": "NORMAL",
            "maintenance_vacuum": False,
        },
        "cancellation": {
            "request_ttl_seconds": 300,
            "poll_interval_seconds": 0.5,
        },
        "observability": {
            "lease_warning_seconds": 20,
            "include_diagnostics_in_stats": True,
        },
        "faults": {
            "sqlite_retry_attempts": 3,
            "sqlite_retry_backoff_seconds": 0.05,
        },
        "streaming": {
            "poll_interval_seconds": 0.5,
            "max_replay_events": 1000,
            "replay_gap_status_code": 409,
            "replay_gap_error_code": "replay_gap",
        },
        "registry": [],
    }


def _merge_defaults(loaded: Any, defaults: Any) -> tuple[Any, bool]:
    if isinstance(defaults, dict):
        changed = not isinstance(loaded, dict)
        source = loaded if isinstance(loaded, dict) else {}
        merged: dict[str, Any] = {}
        for key, value in defaults.items():
            merged[key], child_changed = _merge_defaults(source.get(key), value)
            changed = changed or child_changed or key not in source
        for key, value in source.items():
            if key not in merged:
                merged[key] = value
        return merged, changed
    if isinstance(defaults, list):
        if loaded is None:
            return list(defaults), True
        return loaded, False
    if loaded is None:
        return defaults, True
    return loaded, False


def _auto_public_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _normalize_config(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    server = config["server"]
    host = str(server["host"])
    port = int(server["port"])
    server["port"] = port

    explicit = server.get("public_url_explicit")
    if explicit is None:
        explicit = bool(server.get("public_url")) and server["public_url"] != _auto_public_url(host, port)
        server["public_url_explicit"] = explicit
        changed = True

    if not explicit:
        public_url = _auto_public_url(host, port)
        if server.get("public_url") != public_url:
            server["public_url"] = public_url
            changed = True
    elif server.get("public_url"):
        trimmed = str(server["public_url"]).rstrip("/")
        if trimmed != server["public_url"]:
            server["public_url"] = trimmed
            changed = True
    else:
        server["public_url"] = _auto_public_url(host, port)
        server["public_url_explicit"] = False
        changed = True

    return config, changed


def load_config(path: Path | None = None, *, create_if_missing: bool = True) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        if not create_if_missing:
            raise ConfigError(f"Config file not found: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        config = default_config()
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
        return config
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Unable to read config: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError("Config root must be a mapping")
    merged, changed = _merge_defaults(loaded, default_config())
    merged, normalized = _normalize_config(merged)
    if changed or normalized:
        save_config(merged, path)
    return merged


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def is_localhost(host: str) -> bool:
    return host.lower().strip("[]") in {"127.0.0.1", "localhost", "::1"}


def validate_server_bind(config: dict[str, Any], host: str) -> None:
    allowed = bool(config.get("server", {}).get("allow_remote_hosts", False))
    if not is_localhost(host) and not allowed:
        raise ConfigError(
            "Refusing non-localhost bind. Set server.allow_remote_hosts: true explicitly to allow it."
        )
