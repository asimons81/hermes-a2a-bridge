"""The ``hermes a2a`` argparse command tree."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import client
from .auth import generate_token
from .config import config_path, database_path, load_config, save_config, validate_server_bind
from .diagnostics import diagnose_peer
from .errors import BridgeError, ClientError
from .models import build_agent_card
from .operations import (
    FileOperationError,
    add_remote_url_file_reference,
    attach_file_artifact,
    cleanup_orphaned_file_bytes,
    delete_file,
    file_stats,
    ingest_local_file,
    list_cancellations,
    list_files,
    list_leases,
    maintenance_stats,
    parse_file_metadata_json,
    prune_events,
    recover_expired_leases,
    recover_stale,
    repair_file_storage,
    scan_file_storage,
    show_file,
    verify_file_attachment,
)
from .server import serve, wire
from .store import Store

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TERMINAL_STATES = {
    "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
}


def register_cli(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="a2a_command", required=True)

    sub.add_parser("init", help="Create the local config file and SQLite database")

    card = sub.add_parser("card", help="Print the local Agent Card")
    card.add_argument("--json", action="store_true", help="Emit JSON only")

    run = sub.add_parser("serve", help="Start the local A2A server")
    run.add_argument("--host", default=None, help="Override the configured bind host")
    run.add_argument("--port", type=int, default=None, help="Override the configured bind port")

    token = sub.add_parser("token", help="Manage the server bearer token")
    token_sub = token.add_subparsers(dest="token_command", required=True)
    rotate = token_sub.add_parser("rotate", help="Rotate the local bearer token")
    rotate.add_argument("--show-token", action="store_true", help="Intentionally print the new token")
    rotate.add_argument("--json", action="store_true", help="Emit JSON only")

    discover = sub.add_parser("discover", help="Fetch a remote Agent Card")
    discover.add_argument("url")
    discover.add_argument("--json", action="store_true", help="Emit JSON only")

    doctor = sub.add_parser("doctor", help="Diagnose remote peer compatibility from its Agent Card")
    doctor.add_argument("agent", help="Remote registry name, base URL, or Agent Card URL")
    doctor.add_argument("--token", help="Optional bearer token override")
    doctor.add_argument("--timeout", type=int, default=None, help="Total Agent Card request timeout in seconds")
    doctor.add_argument(
        "--live-probe",
        action="store_true",
        help="Opt in to one tiny diagnostic message send after compatible metadata is found",
    )
    doctor.add_argument(
        "--live-probe-message",
        help="Diagnostic text to send only with --live-probe",
    )
    doctor.add_argument("--json", action="store_true", help="Emit JSON only")

    registry = sub.add_parser("registry", help="Manage named remote agents")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    add = registry_sub.add_parser("add", help="Add or update a named remote agent")
    add.add_argument("name")
    add.add_argument("url")
    add.add_argument("--token", help="Optional bearer token to save locally")
    add.add_argument("--json", action="store_true", help="Emit JSON only")
    listing = registry_sub.add_parser("list", help="List named remote agents")
    listing.add_argument("--json", action="store_true", help="Emit JSON only")
    remove = registry_sub.add_parser("remove", help="Remove a named remote agent")
    remove.add_argument("name")
    remove.add_argument("--json", action="store_true", help="Emit JSON only")

    files_group = sub.add_parser("files", help="Stage and inspect local file attachment metadata")
    files_sub = files_group.add_subparsers(dest="files_command", required=True)
    ingest = files_sub.add_parser("ingest", help="Copy a local file into controlled A2A storage")
    ingest.add_argument("path")
    ingest.add_argument("--task-id")
    ingest.add_argument("--artifact-id")
    ingest.add_argument("--name")
    ingest.add_argument("--mime-type")
    ingest.add_argument("--metadata-json")
    ingest.add_argument("--json", action="store_true", help="Emit JSON only")
    add_url = files_sub.add_parser("add-url", help="Record a metadata-only remote URL file reference")
    add_url.add_argument("url")
    add_url.add_argument("--name")
    add_url.add_argument("--mime-type")
    add_url.add_argument("--size-bytes", type=int)
    add_url.add_argument("--sha256")
    add_url.add_argument("--task-id")
    add_url.add_argument("--artifact-id")
    add_url.add_argument("--metadata-json")
    add_url.add_argument("--json", action="store_true", help="Emit JSON only")
    files_list = files_sub.add_parser("list", help="List staged file metadata")
    files_list.add_argument("--task-id")
    files_list.add_argument("--artifact-id")
    files_list.add_argument("--limit", type=int)
    files_list.add_argument("--json", action="store_true", help="Emit JSON only")
    files_show = files_sub.add_parser("show", help="Show one staged file metadata record")
    files_show.add_argument("file_id")
    files_show.add_argument("--json", action="store_true", help="Emit JSON only")
    files_delete = files_sub.add_parser("delete", help="Delete staged file metadata and optionally bytes")
    files_delete.add_argument("file_id")
    files_delete.add_argument("--delete-bytes", action="store_true")
    files_delete.add_argument("--json", action="store_true", help="Emit JSON only")
    files_verify = files_sub.add_parser("verify", help="Verify one staged file attachment")
    files_verify.add_argument("file_id")
    files_verify.add_argument("--json", action="store_true", help="Emit JSON only")
    files_scan = files_sub.add_parser("scan", help="Scan local file storage health")
    files_scan.add_argument("--json", action="store_true", help="Emit JSON only")
    cleanup_orphans = files_sub.add_parser("cleanup-orphans", help="Delete untracked bytes from controlled storage")
    cleanup_orphans.add_argument("--dry-run", action="store_true", help="Preview changes without deleting bytes")
    cleanup_orphans.add_argument("--confirm", action="store_true", help="Actually delete orphaned stored bytes")
    cleanup_orphans.add_argument("--json", action="store_true", help="Emit JSON only")
    repair = files_sub.add_parser("repair", help="Repair missing local-byte metadata conservatively")
    repair.add_argument("--dry-run", action="store_true", help="Preview changes without deleting metadata")
    repair.add_argument("--confirm", action="store_true", help="Actually remove missing local-byte metadata")
    repair.add_argument("--json", action="store_true", help="Emit JSON only")
    attach_artifact = files_sub.add_parser(
        "attach-artifact",
        help="Attach an already staged file to an existing local task as an artifact reference",
    )
    attach_artifact.add_argument("file_id")
    attach_artifact.add_argument("task_id")
    attach_artifact.add_argument("--artifact-id")
    attach_artifact.add_argument("--name")
    attach_artifact.add_argument("--json", action="store_true", help="Emit JSON only")
    fetch_metadata = files_sub.add_parser(
        "fetch-metadata",
        help="Fetch staged file metadata from an A2A bridge HTTP route",
    )
    fetch_metadata.add_argument("file_id")
    fetch_metadata.add_argument("--agent", required=True, help="Remote registry name or base URL")
    fetch_metadata.add_argument("--token", help="Optional remote bearer token override")
    fetch_metadata.add_argument("--json", action="store_true", help="Emit JSON only")
    download = files_sub.add_parser(
        "download",
        help="Download staged file bytes from an A2A bridge HTTP route",
    )
    download.add_argument("file_id")
    download.add_argument("output_path")
    download.add_argument("--agent", required=True, help="Remote registry name or base URL")
    download.add_argument("--token", help="Optional remote bearer token override")
    download.add_argument("--json", action="store_true", help="Emit JSON only")
    files_stats = files_sub.add_parser("stats", help="Show staged file metadata statistics")
    files_stats.add_argument("--json", action="store_true", help="Emit JSON only")

    maintenance = sub.add_parser("maintenance", help="Inspect and maintain the local A2A database")
    maintenance_sub = maintenance.add_subparsers(dest="maintenance_command", required=True)
    for name, help_text in (
        ("stats", "Show local task and event database statistics"),
        ("prune-events", "Apply the configured event retention policy"),
        ("recover-stale", "Recover stale non-terminal tasks"),
        ("leases", "List task ownership leases"),
        ("cancellations", "List cooperative cancellation requests"),
        ("recover-leases", "Recover tasks whose ownership leases expired"),
    ):
        item = maintenance_sub.add_parser(name, help=help_text)
        item.add_argument("--json", action="store_true", help="Emit JSON only")

    send = sub.add_parser("send", help="Send one text task to a remote agent", allow_abbrev=False)
    send.add_argument("agent")
    send.add_argument("message")
    send.add_argument("--file-id", action="append", default=[], help="Attach a stored Hermes file ID reference")
    send.add_argument("--token", help="Override any saved bearer token")
    send.add_argument("--json", action="store_true", help="Emit JSON only")

    stream = sub.add_parser("stream", help="Stream one text task from a remote agent", allow_abbrev=False)
    stream.add_argument("agent")
    stream.add_argument("message")
    stream.add_argument("--file-id", action="append", default=[], help="Attach a stored Hermes file ID reference")
    stream.add_argument("--token", help="Override any saved bearer token")
    stream.add_argument("--json", action="store_true", help="Emit one JSON event per line")

    subscribe = sub.add_parser("subscribe", help="Subscribe to updates for an active task")
    subscribe.add_argument("task_id")
    subscribe.add_argument("--agent", help="Remote registry name or URL. Omit for the local server.")
    subscribe.add_argument("--token", help="Optional bearer token override")
    subscribe.add_argument("--last-event-id", type=int, help="Resume after this SQLite event ID")
    subscribe.add_argument("--json", action="store_true", help="Emit one JSON event per line")

    tasks = sub.add_parser("tasks", help="List local or remote tasks")
    tasks.add_argument("--agent", help="Remote registry name or URL. Omit for local tasks.")
    tasks.add_argument("--token", help="Optional remote bearer token override")
    tasks.add_argument("--json", action="store_true", help="Emit JSON only")

    task = sub.add_parser("task", help="Get one local or remote task")
    task.add_argument("task_id")
    task.add_argument("--agent", help="Remote registry name or URL. Omit for local task lookup.")
    task.add_argument("--token", help="Optional remote bearer token override")
    task.add_argument("--json", action="store_true", help="Emit JSON only")

    cancel = sub.add_parser("cancel", help="Cancel one local or remote task")
    cancel.add_argument("task_id")
    cancel.add_argument("--agent", help="Remote registry name or URL. Omit for local task cancellation.")
    cancel.add_argument("--token", help="Optional remote bearer token override")
    cancel.add_argument("--json", action="store_true", help="Emit JSON only")


def _resolved(store: Store, value: str, token: str | None) -> tuple[str, str | None]:
    if value.startswith(("http://", "https://")):
        return value, token
    row = store.registry_get(value)
    if not row:
        raise ValueError(f"Unknown registry name: {value}")
    return row["url"], token if token is not None else row["token"]


def _print(value: Any, as_json: bool = False) -> None:
    if as_json or isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(value)


def _print_error(message: str, *, as_json: bool, code: str = "cli_error") -> None:
    if as_json:
        print(json.dumps({"success": False, "error": message, "code": code}, ensure_ascii=False))
    else:
        print(f"Error: {message}", file=sys.stderr)


def _validate_registry_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError(
            "Registry names must start with a letter or digit and use only letters, digits, dot, underscore, or hyphen"
        )


def _task_text(task: dict[str, Any] | None) -> str | None:
    try:
        parts = task["status"]["message"]["parts"]
    except (KeyError, TypeError):
        return None
    texts = [part.get("text") for part in parts if isinstance(part, dict) and part.get("text")]
    return "\n".join(texts) if texts else None


def _data_summary(value: Any) -> str:
    if isinstance(value, dict):
        return f"[data part: object, {len(value)} keys]"
    if isinstance(value, list):
        return f"[data part: array, {len(value)} items]"
    return "[data part]"


def _file_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return "[file]"
    name = value.get("name") or value.get("fileId") or "attachment"
    mime = value.get("mimeType") or "unknown"
    size = value.get("sizeBytes")
    file_id = value.get("fileId")
    size_text = f"{size} bytes" if isinstance(size, int) else "unknown size"
    suffix = f", id {file_id}" if file_id else ""
    mode = ", metadata-only URL reference" if value.get("metadataOnly") else ""
    return f"[file: {name}, {mime}, {size_text}{suffix}{mode}]"


def _part_summaries(parts: list[dict[str, Any]]) -> list[str]:
    summaries: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("text"):
            summaries.append(str(part["text"]))
        elif "data" in part:
            summaries.append(_data_summary(part["data"]))
        elif "file" in part:
            summaries.append(_file_summary(part["file"]))
    return summaries


def _task_artifact_summaries(task: dict[str, Any] | None) -> list[str]:
    if not isinstance(task, dict):
        return []
    summaries: list[str] = []
    artifacts = task.get("artifacts", [])
    if not isinstance(artifacts, list):
        return summaries
    for artifact in artifacts:
        if isinstance(artifact, dict):
            parts = artifact.get("parts", [])
            if isinstance(parts, list):
                summaries.extend(_part_summaries(parts))
    return summaries


def _task_input_file_summaries(task: dict[str, Any] | None) -> list[str]:
    if not isinstance(task, dict):
        return []
    references = task.get("metadata", {}).get("inputFileReferences")
    if not isinstance(references, list):
        return []
    return [_file_summary(item) for item in references if isinstance(item, dict)]


def _format_agent(card: dict[str, Any]) -> dict[str, Any]:
    return {"agent": card}


def _format_task(task: dict[str, Any]) -> dict[str, Any]:
    return {"task": task, "resultText": _task_text(task)}


def _format_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"tasks": tasks}


def _render_text(result: dict[str, Any]) -> None:
    if "agent" in result:
        agent = result["agent"]
        if "removed" in agent:
            _print(f"{agent['name']}: {'removed' if agent['removed'] else 'not found'}")
        else:
            suffix = f" (token saved: {'yes' if agent.get('hasToken') else 'no'})" if "hasToken" in agent else ""
            _print(f"{agent['name']} -> {agent['url']}{suffix}")
        return
    if "tasks" in result:
        for task in result["tasks"]:
            _print(f"{task['id']}: {task['status']['state']}")
        return
    if "task" in result:
        task = result["task"]
        _print(f"{task['id']}: {task['status']['state']}")
        if result.get("resultText"):
            _print(result["resultText"])
        for summary in _task_input_file_summaries(task):
            _print(summary)
        for summary in _task_artifact_summaries(task):
            if summary != result.get("resultText"):
                _print(summary)
        return
    if "agents" in result:
        for item in result["agents"]:
            _print(f"{item['name']}: {item['url']} (token saved: {'yes' if item['hasToken'] else 'no'})")
        return


def _render_maintenance(command: str, result: dict[str, Any]) -> None:
    if command == "stats":
        _print(f"Tasks: {result['task_count']}")
        _print(f"Events: {result['event_count']}")
        _print(f"Registry entries: {result['registry_count']}")
        _print(f"Event IDs: {result['oldest_event_id']} .. {result['newest_event_id']}")
        _print(f"Leases: {result['lease_count']} ({result['expired_lease_count']} expired)")
        _print(
            f"Cancellations: {result['pending_cancellation_count']} pending, "
            f"{result['expired_cancellation_count']} expired"
        )
        _print(
            f"File attachments: {result['file_attachment_count']} "
            f"({result['file_attachment_bytes']} bytes metadata total)"
        )
        _print(
            f"SQLite: {result['journal_mode']} journal, {result['busy_timeout_ms']} ms busy timeout, "
            f"{result['database_size_bytes']} bytes"
        )
    elif command == "prune-events":
        _print(
            f"Pruned {result['deleted_count']} events; {result['remaining_count']} remain "
            f"across {result['affected_task_count']} affected tasks."
        )
    elif command in {"recover-stale", "recover-leases"}:
        _print(
            f"Recovered {result['recovered_count']} stale tasks; "
            f"skipped {result['skipped_count']}."
        )
    elif command == "leases":
        _print(f"Leases: {result['lease_count']} ({result['expired_lease_count']} expired)")
        for lease in result["leases"]:
            _print(
                f"{lease['task_id']}: {lease['owner_instance_id']} pid={lease['owner_pid']} "
                f"heartbeat_age={lease['heartbeat_age_seconds']:.1f}s "
                f"expires_in={lease['seconds_until_expiry']:.1f}s "
                f"warnings=expiring:{lease['lease_expiring_soon']},stale:{lease['heartbeat_stale']},expired:{lease['expired']}"
            )
    else:
        _print(
            f"Cancellation requests: {result['cancellation_count']} "
            f"({result['pending_cancellation_count']} pending, {result['expired_cancellation_count']} expired)"
        )
        for item in result["cancellations"]:
            _print(
                f"{item['id']}: task={item['task_id']} status={item['status']} "
                f"owner={item['owner_instance_id']} expires={item['expires_at']}"
            )


def _render_files(command: str, result: dict[str, Any]) -> None:
    if command == "ingest":
        file = result["file"]
        _print(f"Stored {file['fileId']}: {file['name']} ({file['sizeBytes']} bytes)")
    elif command == "add-url":
        file = result["file"]
        size = file.get("sizeBytes")
        size_text = f"{size} bytes" if isinstance(size, int) else "unknown size"
        source = f" -> {file['sourceUrl']}" if file.get("sourceUrl") else ""
        _print(f"Recorded metadata-only URL reference {file['fileId']}: {file['name']} ({size_text}){source}")
    elif command == "list":
        for file in result["files"]:
            _print(f"{file['fileId']}: {file['name']} ({file['sizeBytes']} bytes, {file.get('mimeType', 'unknown')})")
    elif command == "show":
        file = result["file"]
        _print(f"{file['fileId']}: {file['name']}")
        _print(f"MIME: {file.get('mimeType', 'unknown')}")
        size = file.get("sizeBytes")
        _print(f"Size: {size if isinstance(size, int) else 'unknown'} bytes")
        _print(f"SHA-256: {file.get('sha256') or 'unknown'}")
        if file.get("metadataOnly"):
            _print("Source: metadata-only remote URL reference")
            if file.get("sourceUrl"):
                _print(f"URL: {file['sourceUrl']}")
        _print(f"Bytes present: {'yes' if file.get('bytesPresent') else 'no'}")
    elif command == "delete":
        _print(
            f"{result['fileId']}: metadata deleted={result['metadataDeleted']}, "
            f"bytes deleted={result['bytesDeleted']}"
        )
    elif command == "verify":
        _print(
            f"{result['fileId']}: {result['status']} "
            f"(bytes available: {'yes' if result.get('bytesAvailable') else 'no'})"
        )
    elif command == "scan":
        _print(
            f"File storage: {result['localAttachmentCount']} local, "
            f"{result['remoteUrlMetadataCount']} remote URL metadata records"
        )
        _print(
            f"Issues: {result['orphanedByteCount']} orphaned byte files, "
            f"{len(result['metadataIssues'])} metadata issues, "
            f"{result['artifactReferenceIssueCount']} artifact reference issues"
        )
    elif command == "cleanup-orphans":
        action = "would delete" if result["dryRun"] else "deleted"
        _print(f"Cleanup orphans: {action} {result['orphanedByteCount'] if result['dryRun'] else result['deletedByteCount']} byte files")
    elif command == "repair":
        action = "would remove" if result["dryRun"] else "removed"
        metadata = result["metadataRepair"]
        _print(f"Repair: {action} {metadata['missingMetadataCount'] if result['dryRun'] else metadata['removedMetadataCount']} metadata records")
    elif command == "attach-artifact":
        artifact = result["artifact"]
        summaries = _part_summaries(artifact.get("parts", []))
        _print(f"Attached {artifact['artifactId']} to {result['taskId']}")
        for summary in summaries:
            _print(summary)
    elif command == "fetch-metadata":
        file = result["file"]
        _print(f"{file['fileId']}: {file['name']} ({file['sizeBytes']} bytes, {file.get('mimeType', 'unknown')})")
    elif command == "download":
        _print(f"Downloaded {result['fileId']} to {result['outputPath']} ({result['bytesWritten']} bytes)")
    else:
        _print(
            f"File attachments: {result['file_attachment_count']} "
            f"({result['file_attachment_bytes']} bytes metadata total)"
        )


def _render_doctor(result: dict[str, Any]) -> None:
    _print(f"Peer Doctor: {result['status']}")
    live_probe = result.get("live_probe", {})
    mode = "live-probed" if live_probe.get("attempted") else "metadata-only"
    _print(f"Mode: {mode}")
    if result.get("name"):
        _print(f"Name: {result['name']}")
    protocol = result.get("protocol", {})
    if protocol.get("binding") or protocol.get("version"):
        _print(f"Protocol: {protocol.get('binding') or 'unknown'} {protocol.get('version') or ''}".rstrip())
    usable = [
        name for name, enabled in result.get("capabilities", {}).items()
        if enabled
    ]
    _print(f"Usable: {', '.join(usable) if usable else 'none detected'}")
    if live_probe.get("enabled"):
        if live_probe.get("attempted"):
            _print("Live probe: enabled")
            _print(f"Message send: {'passed' if live_probe.get('message_send') else 'failed'}")
            if live_probe.get("task_id"):
                _print(f"Task ID: {live_probe['task_id']}")
            if live_probe.get("task_status"):
                _print(f"Task status: {live_probe['task_status']}")
            if live_probe.get("task_get") is not None:
                _print(f"Task lookup: {'passed' if live_probe.get('task_get') else 'failed'}")
        else:
            _print(f"Live probe: skipped ({live_probe.get('reason', 'not attempted')})")
        _print("Live probe sends a tiny diagnostic message. It does not send files, fetch files, cancel tasks, or stream.")
        for warning in live_probe.get("warnings", []):
            _print(f"Probe warning: {_compact_probe_issue(warning)}")
        for error in live_probe.get("errors", []):
            _print(f"Probe error: {_compact_probe_issue(error)}")
    else:
        _print("Live probe: disabled")
    for error in result.get("errors", []):
        _print(f"Blocker: {error}")
    for warning in result.get("warnings", []):
        _print(f"Warning: {warning}")
    if result.get("recommendations"):
        _print(f"Next: {result['recommendations'][0]}")


def _compact_probe_issue(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("message") or value)
    return str(value)


def _local_files(store: Store, config: dict[str, Any], args) -> dict[str, Any]:
    if args.files_command == "ingest":
        metadata = parse_file_metadata_json(args.metadata_json)
        return ingest_local_file(
            args.path,
            store,
            config,
            task_id=args.task_id,
            artifact_id=args.artifact_id,
            name=args.name,
            declared_mime_type=args.mime_type,
            metadata=metadata,
        )
    if args.files_command == "add-url":
        metadata = parse_file_metadata_json(args.metadata_json)
        return add_remote_url_file_reference(
            args.url,
            store,
            config,
            task_id=args.task_id,
            artifact_id=args.artifact_id,
            name=args.name,
            declared_mime_type=args.mime_type,
            size_bytes=args.size_bytes,
            sha256=args.sha256,
            metadata=metadata,
        )
    if args.files_command == "list":
        return list_files(
            store,
            task_id=args.task_id,
            artifact_id=args.artifact_id,
            limit=args.limit,
        )
    if args.files_command == "show":
        return show_file(store, args.file_id)
    if args.files_command == "delete":
        return delete_file(store, config, args.file_id, delete_bytes=args.delete_bytes)
    if args.files_command == "verify":
        return verify_file_attachment(store, config, args.file_id)
    if args.files_command == "scan":
        return scan_file_storage(store, config)
    if args.files_command == "cleanup-orphans":
        if args.dry_run and args.confirm:
            raise FileOperationError("Use either --dry-run or --confirm, not both.", "invalid_cleanup_flags")
        return cleanup_orphaned_file_bytes(store, config, dry_run=not args.confirm)
    if args.files_command == "repair":
        if args.dry_run and args.confirm:
            raise FileOperationError("Use either --dry-run or --confirm, not both.", "invalid_repair_flags")
        return repair_file_storage(store, config, dry_run=not args.confirm)
    if args.files_command == "attach-artifact":
        return attach_file_artifact(
            store,
            config,
            args.file_id,
            args.task_id,
            artifact_id=args.artifact_id,
            name=args.name,
        )
    return file_stats(store, config)


def _local_tasks(command: str, store: Store, args) -> dict[str, Any]:
    if command == "tasks":
        return _format_tasks([wire(task) for task in store.list_tasks()])
    if command == "task":
        task = store.get_task(args.task_id)
        if not task:
            raise ValueError("Task not found")
        return _format_task(wire(task))

    task = store.get_task(args.task_id)
    if not task:
        raise ValueError("Task not found")
    if task.status.state.value in TERMINAL_STATES:
        raise ValueError("Task can no longer be canceled")
    lease = store.get_task_lease(args.task_id)
    if lease and datetime.fromisoformat(lease["lease_expires_at"]).astimezone(timezone.utc) > datetime.now(timezone.utc):
        raise ValueError("Task is owned by an active server instance; cancel it through that server")
    canceled = store.cancel_task(args.task_id)
    if not canceled:
        raise ValueError("Task not found")
    return _format_task(wire(canceled))


async def _remote(args, store: Store) -> dict[str, Any]:
    command = args.a2a_command
    if command == "discover":
        return _format_agent(await client.fetch_agent_card(args.url))
    if command == "doctor":
        base, token = _resolved(store, args.agent, getattr(args, "token", None))
        return await diagnose_peer(
            base,
            token=token,
            timeout_seconds=args.timeout,
            live_probe=args.live_probe,
            probe_message=args.live_probe_message,
        )

    base, token = _resolved(store, args.agent, getattr(args, "token", None))
    card = await client.fetch_agent_card(base)
    endpoint = client.agent_endpoint(card)
    if command == "files":
        if args.files_command == "fetch-metadata":
            return {"success": True, "file": await client.get_file_metadata(endpoint, args.file_id, token)}
        body = await client.download_file(endpoint, args.file_id, token, args.output_path)
        return {
            "success": True,
            "fileId": args.file_id,
            "outputPath": str(Path(args.output_path)),
            "bytesWritten": len(body),
        }
    if command == "send":
        return _format_task(await client.send_message(
            endpoint, args.message, token, file_ids=getattr(args, "file_id", None),
        ))
    if command == "tasks":
        return _format_tasks(await client.list_tasks(endpoint, token))
    if command == "task":
        return _format_task(await client.get_task(endpoint, args.task_id, token))
    return _format_task(await client.cancel_task(endpoint, args.task_id, token))


def _render_stream_event(envelope: dict[str, Any]) -> None:
    event = envelope["data"]
    prefix = f"[event {envelope['id']}] " if envelope.get("id") is not None else ""
    if "task" in event:
        task = event["task"]
        _print(f"{prefix}{task['id']}: {task['status']['state']}")
        return
    if "statusUpdate" in event:
        update = event["statusUpdate"]
        _print(f"{prefix}{update['taskId']}: {update['status']['state']}")
        text = _task_text({"status": update["status"]})
        if text:
            _print(text)
        return
    if "artifactUpdate" in event:
        artifact = event["artifactUpdate"]["artifact"]
        summaries = _part_summaries(artifact.get("parts", []))
        if summaries:
            _print(f"{prefix}{' '.join(summaries)}")
        else:
            _print(f"{prefix}{event['artifactUpdate']['taskId']}: artifact update")


async def _stream_cli(args, store: Store, config: dict[str, Any]) -> None:
    if args.a2a_command == "stream":
        base, token = _resolved(store, args.agent, args.token)
        endpoint = client.agent_endpoint(await client.fetch_agent_card(base))
        events = client.stream_message(endpoint, args.message, token, file_ids=getattr(args, "file_id", None))
    else:
        if args.agent:
            base, token = _resolved(store, args.agent, args.token)
            endpoint = client.agent_endpoint(await client.fetch_agent_card(base))
        else:
            endpoint = config["server"]["public_url"]
            token = args.token if args.token is not None else config["server"].get("auth_token")
        events = client.subscribe_task(endpoint, args.task_id, token, getattr(args, "last_event_id", None))
    async for event in events:
        if args.json:
            print(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        else:
            _render_stream_event(event)


def a2a_command(args) -> int:
    as_json = bool(getattr(args, "json", False))
    command = args.a2a_command
    try:
        if command in {"send", "stream"}:
            client.validate_file_ids(getattr(args, "file_id", None))

        if command == "init":
            config = load_config(create_if_missing=True)
            Store(database_path(), config.get("sqlite", {}), config.get("faults", {}))
            _print({"config": str(config_path()), "database": str(database_path()), "initialized": True}, True)
            return 0

        if command == "discover":
            result = asyncio.run(_remote(args, Store(database_path())))
            if as_json:
                _print(result, True)
            else:
                _render_text(result)
            return 0

        config = load_config()
        store = Store(database_path(), config.get("sqlite", {}), config.get("faults", {}))

        if command == "card":
            card = wire(build_agent_card(config))
            if as_json:
                _print(card, True)
            else:
                _print(f"{card['name']} ({card['version']})")
                _print(card["url"])
                _print(card["description"])
            return 0

        if command == "serve":
            host = args.host or config["server"]["host"]
            validate_server_bind(config, host)
            serve(config, host, args.port)
            return 0

        if command == "token":
            config["server"]["auth_token"] = generate_token()
            save_config(config)
            result = {
                "success": True,
                "message": "Bearer token rotated. Old tokens stop working immediately.",
            }
            if args.show_token:
                result["token"] = config["server"]["auth_token"]
            if as_json:
                _print(result, True)
            elif args.show_token:
                _print(f"{result['message']}\nNew token: {result['token']}")
            else:
                _print(result["message"])
            return 0

        if command == "registry":
            if args.registry_command == "add":
                _validate_registry_name(args.name)
                url = client.require_http_url(args.url)
                store.registry_add(args.name, url, args.token)
                result = {"agent": {"name": args.name, "url": url, "hasToken": bool(args.token)}}
            elif args.registry_command == "list":
                result = {"agents": store.registry_list()}
            else:
                _validate_registry_name(args.name)
                result = {"agent": {"name": args.name, "removed": store.registry_remove(args.name)}}
            if as_json:
                _print(result, True)
            else:
                _render_text(result)
            return 0

        if command == "doctor":
            result = asyncio.run(_remote(args, store))
            if as_json:
                _print(result, True)
            else:
                _render_doctor(result)
            return 0 if result.get("ok") else 1

        if command == "maintenance":
            if args.maintenance_command == "stats":
                result = maintenance_stats(store, config)
            elif args.maintenance_command == "prune-events":
                result = prune_events(store, config)
            elif args.maintenance_command == "recover-stale":
                result = recover_stale(store, config)
            elif args.maintenance_command == "leases":
                result = list_leases(store, config)
            elif args.maintenance_command == "cancellations":
                result = list_cancellations(store)
            else:
                result = recover_expired_leases(store, config)
            if as_json:
                _print(result, True)
            else:
                _render_maintenance(args.maintenance_command, result)
            return 0

        if command == "files":
            if args.files_command in {"fetch-metadata", "download"}:
                result = asyncio.run(_remote(args, store))
            else:
                result = _local_files(store, config, args)
            if as_json:
                _print(result, True)
            else:
                _render_files(args.files_command, result)
            return 0

        if command in {"stream", "subscribe"}:
            asyncio.run(_stream_cli(args, store, config))
            return 0

        if command in {"tasks", "task", "cancel"} and not args.agent:
            result = _local_tasks(command, store, args)
        else:
            result = asyncio.run(_remote(args, store))
        if as_json:
            _print(result, True)
        else:
            _render_text(result)
        return 0
    except (BridgeError, ValueError) as exc:
        if as_json and isinstance(exc, ClientError) and exc.payload:
            _print(exc.payload, True)
            return 1
        if as_json and isinstance(exc, FileOperationError):
            _print(exc.payload, True)
            return 1
        _print_error(str(exc), as_json=as_json, code=getattr(exc, "code", "cli_error"))
        return 1
