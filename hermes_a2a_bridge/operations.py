"""Shared database maintenance operations for startup and CLI use."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import files
from .auth import redact_secrets
from .errors import BridgeError
from .models import TaskState
from .store import Store

RECOVERABLE_STATES = {TaskState.SUBMITTED, TaskState.WORKING, TaskState.INPUT_REQUIRED}
RECOVERY_TARGETS = {TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}


class FileOperationError(BridgeError):
    def __init__(self, message: str, code: str = "file_operation_failed"):
        super().__init__(message)
        self.code = code
        self.payload = {"success": False, "error": message, "code": code}


def _file_error_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "disabled" in message:
        return "remote_url_references_disabled"
    if "auto-fetch" in message or "auto fetch" in message:
        return "remote_url_auto_fetch_unsupported"
    if "http(s)" in message or "url" in message:
        return "unsupported_remote_url"
    if "sha-256" in message:
        return "invalid_sha256"
    if "does not exist" in message:
        return "local_file_not_found"
    if "regular file" in message:
        return "local_file_not_regular"
    if "size limit" in message or "size must" in message:
        return "file_too_large"
    if "quota" in message:
        return "storage_quota_exceeded"
    if "unknown" in message and "mime" in message:
        return "unknown_mime_type"
    if "mime" in message:
        return "mime_type_not_allowed"
    if "symlink" in message or "reparse" in message:
        return "local_file_unsafe"
    return "file_operation_failed"


def parse_file_metadata_json(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise FileOperationError("metadata-json must be a valid JSON object", "invalid_metadata_json") from exc
    if not isinstance(parsed, dict):
        raise FileOperationError("metadata-json must be a JSON object", "invalid_metadata_json")
    return parsed


def ingest_local_file(
    path: Path | str,
    store: Store,
    config: dict[str, Any],
    *,
    task_id: str | None = None,
    artifact_id: str | None = None,
    name: str | None = None,
    declared_mime_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        source = files.validate_local_ingest_path(path)
        source_size = source.stat(follow_symlinks=False).st_size
        files.validate_file_size(source_size, config)
        stats = store.file_storage_stats()
        quota = int(config.get("files", {}).get("max_total_storage_bytes", 1073741824))
        if int(stats["file_attachment_bytes"]) + int(source_size) > quota:
            raise FileOperationError(
                "Attachment storage quota would be exceeded",
                "storage_quota_exceeded",
            )
        original_name = name or source.name
        safe_name = files.sanitize_filename(original_name)
        mime_type = files.guess_mime_type(safe_name, declared_mime_type)
        files.validate_mime_type(mime_type, config)
        file_id = files.generate_file_id()
        storage_root = files.resolve_storage_root(config)
        written = files.write_attachment_file_atomic(storage_root, file_id, source, config)
        try:
            row = store.add_file_attachment(
                file_id=file_id,
                task_id=task_id,
                artifact_id=artifact_id,
                filename=original_name,
                safe_filename=safe_name,
                mime_type=mime_type,
                declared_mime_type=declared_mime_type,
                size_bytes=written["size_bytes"],
                sha256=written["sha256"],
                storage_path=written["storage_path"],
                source="local_cli",
                metadata=metadata or {},
            )
        except Exception:
            files.delete_attachment_file(storage_root, file_id, config)
            raise
        return {"success": True, "file": files.public_file_metadata(row)}
    except FileOperationError:
        raise
    except files.FileAttachmentError as exc:
        raise FileOperationError(str(exc), _file_error_code(exc)) from exc
    except Exception as exc:
        token = config.get("server", {}).get("auth_token")
        raise FileOperationError(redact_secrets(exc, token)) from exc


def add_remote_url_file_reference(
    url: str,
    store: Store,
    config: dict[str, Any],
    *,
    task_id: str | None = None,
    artifact_id: str | None = None,
    name: str | None = None,
    declared_mime_type: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        files.validate_remote_url_reference(url, config)
        original_name = name or Path(files.sanitize_source_url(url) or "remote-url").name or "remote-url"
        safe_name = files.sanitize_filename(original_name)
        mime_type = files.guess_mime_type(safe_name, declared_mime_type)
        files.validate_mime_type(mime_type, config)
        if size_bytes is not None:
            files.validate_file_size(size_bytes, config)
        files.validate_sha256(sha256)
        row = store.add_file_attachment(
            file_id=files.generate_file_id(),
            task_id=task_id,
            artifact_id=artifact_id,
            filename=original_name,
            safe_filename=safe_name,
            mime_type=mime_type,
            declared_mime_type=declared_mime_type,
            size_bytes=size_bytes,
            sha256=sha256.lower() if isinstance(sha256, str) else None,
            storage_path=None,
            source="remote_url",
            source_url=url.strip(),
            metadata=metadata or {},
        )
        return {"success": True, "file": files.public_file_metadata(row)}
    except FileOperationError:
        raise
    except files.FileAttachmentError as exc:
        raise FileOperationError(str(exc), _file_error_code(exc)) from exc
    except Exception as exc:
        token = config.get("server", {}).get("auth_token")
        raise FileOperationError(redact_secrets(exc, token)) from exc


def list_files(
    store: Store,
    *,
    task_id: str | None = None,
    artifact_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "files": [
            files.public_file_metadata(row)
            for row in store.list_file_attachments(task_id=task_id, artifact_id=artifact_id, limit=limit)
        ],
    }


def show_file(store: Store, file_id: str) -> dict[str, Any]:
    row = store.get_file_attachment(file_id)
    if row is None:
        raise FileOperationError("File attachment metadata was not found", "file_not_found")
    result = files.public_file_metadata(row)
    storage_path = row.get("storage_path")
    result["bytesPresent"] = bool(storage_path and Path(storage_path).is_file())
    return {"success": True, "file": result}


def _safe_file_item(row: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    item = files.public_file_metadata(row)
    if status:
        item["status"] = status
    return item


def _is_reparse_or_symlink(path: Path) -> bool:
    return files._has_reparse_point(path) or path.is_symlink()


def _resolve_recorded_storage_path(config: dict[str, Any], row: dict[str, Any]) -> Path:
    storage_path = row.get("storage_path")
    if not isinstance(storage_path, str) or not storage_path:
        raise files.FileAttachmentError("Attachment storage path is missing")
    root = files.resolve_storage_root(config)
    path = files.validate_storage_path(root, storage_path)
    if path.exists() and _is_reparse_or_symlink(path):
        raise files.FileAttachmentError("Attachment storage path is unsafe")
    return path


def verify_file_attachment(store: Store, config: dict[str, Any], file_id: str) -> dict[str, Any]:
    row = store.get_file_attachment(file_id)
    if row is None:
        return {
            "success": False,
            "fileId": file_id,
            "status": "file_not_found",
            "bytesAvailable": False,
            "metadataOnly": False,
            "safe": False,
            "code": "file_not_found",
            "error": "File attachment metadata was not found",
        }
    if row.get("source") == "remote_url":
        return {
            "success": True,
            "fileId": file_id,
            "status": "metadata_only",
            "bytesAvailable": False,
            "metadataOnly": True,
            "sizeBytes": row.get("size_bytes"),
            "sha256": row.get("sha256"),
            "safe": not bool(row.get("storage_path")),
        }
    try:
        path = _resolve_recorded_storage_path(config, row)
    except files.FileAttachmentError:
        return {
            "success": True,
            "fileId": file_id,
            "status": "unsafe_path",
            "bytesAvailable": False,
            "metadataOnly": False,
            "sizeBytes": row.get("size_bytes"),
            "sha256": row.get("sha256"),
            "safe": False,
        }
    if not path.exists() or not path.is_file():
        return {
            "success": True,
            "fileId": file_id,
            "status": "missing_bytes",
            "bytesAvailable": False,
            "metadataOnly": False,
            "sizeBytes": row.get("size_bytes"),
            "sha256": row.get("sha256"),
            "safe": True,
        }
    try:
        actual_size = int(path.stat(follow_symlinks=False).st_size)
    except OSError:
        return {
            "success": True,
            "fileId": file_id,
            "status": "missing_bytes",
            "bytesAvailable": False,
            "metadataOnly": False,
            "sizeBytes": row.get("size_bytes"),
            "sha256": row.get("sha256"),
            "safe": True,
        }
    expected_size = row.get("size_bytes")
    if expected_size is not None and actual_size != int(expected_size):
        return {
            "success": True,
            "fileId": file_id,
            "status": "size_mismatch",
            "bytesAvailable": True,
            "metadataOnly": False,
            "sizeBytes": expected_size,
            "actualSizeBytes": actual_size,
            "sha256": row.get("sha256"),
            "safe": False,
        }
    expected_sha = row.get("sha256")
    if expected_sha:
        try:
            actual_sha = files.sha256_file(path)
        except OSError:
            return {
                "success": True,
                "fileId": file_id,
                "status": "missing_bytes",
                "bytesAvailable": False,
                "metadataOnly": False,
                "sizeBytes": expected_size,
                "sha256": expected_sha,
                "safe": True,
            }
        if actual_sha != expected_sha:
            return {
                "success": True,
                "fileId": file_id,
                "status": "checksum_mismatch",
                "bytesAvailable": True,
                "metadataOnly": False,
                "sizeBytes": expected_size,
                "sha256": expected_sha,
                "actualSha256": actual_sha,
                "safe": False,
            }
    return {
        "success": True,
        "fileId": file_id,
        "status": "ok",
        "bytesAvailable": True,
        "metadataOnly": False,
        "sizeBytes": expected_size,
        "sha256": expected_sha,
        "safe": True,
    }


def _metadata_paths(store: Store, config: dict[str, Any]) -> set[Path]:
    paths: set[Path] = set()
    for row in store.list_file_attachments():
        storage_path = row.get("storage_path")
        if not storage_path:
            continue
        try:
            path = files.validate_storage_path(files.resolve_storage_root(config), storage_path)
        except files.FileAttachmentError:
            continue
        paths.add(path)
    return paths


def _stored_byte_candidates(config: dict[str, Any]) -> list[Path]:
    root = files.resolve_storage_root(config)
    if not root.exists():
        return []
    try:
        root = files.validate_storage_path(root, root)
    except files.FileAttachmentError:
        return []
    candidates: list[Path] = []
    for path in root.rglob("*"):
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError:
            continue
        if _is_reparse_or_symlink(path) or not path.is_file():
            continue
        try:
            resolved = files.validate_storage_path(root, path)
        except files.FileAttachmentError:
            continue
        candidates.append(resolved)
    return candidates


def _relative_storage_key(config: dict[str, Any], path: Path) -> str:
    root = files.resolve_storage_root(config)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "<outside-storage-root>"


def _orphaned_byte_paths(store: Store, config: dict[str, Any]) -> list[Path]:
    recorded = _metadata_paths(store, config)
    return [path for path in _stored_byte_candidates(config) if path not in recorded]


def find_orphaned_file_bytes(store: Store, config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"storageKey": _relative_storage_key(config, path), "sizeBytes": path.stat(follow_symlinks=False).st_size}
        for path in _orphaned_byte_paths(store, config)
    ]


def find_orphaned_file_metadata(store: Store, config: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in store.list_file_attachments():
        verification = verify_file_attachment(store, config, row["id"])
        status = verification["status"]
        if row.get("source") == "remote_url" and row.get("storage_path"):
            issues.append(_safe_file_item(row, status="remote_url_with_local_path"))
        elif status in {"missing_bytes", "unsafe_path", "checksum_mismatch", "size_mismatch"}:
            issues.append(_safe_file_item(row, status=status))
    return issues


def find_file_artifact_reference_issues(store: Store, config: dict[str, Any]) -> list[dict[str, Any]]:
    known = {row["id"] for row in store.list_file_attachments()}
    issues: list[dict[str, Any]] = []

    def inspect_value(value: Any, *, task_id: str | None = None, event_id: int | None = None) -> None:
        if isinstance(value, dict):
            file_value = value.get("file")
            if isinstance(file_value, dict):
                file_id = file_value.get("fileId")
                if isinstance(file_id, str) and file_id.startswith("file_") and file_id not in known:
                    issue = {"fileId": file_id, "status": "missing_file_metadata"}
                    if task_id is not None:
                        issue["taskId"] = task_id
                    if event_id is not None:
                        issue["eventId"] = event_id
                    issues.append(issue)
            for child in value.values():
                inspect_value(child, task_id=task_id, event_id=event_id)
        elif isinstance(value, list):
            for child in value:
                inspect_value(child, task_id=task_id, event_id=event_id)

    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT id, response_json FROM tasks WHERE response_json IS NOT NULL"):
            try:
                response = json.loads(row["response_json"])
            except json.JSONDecodeError:
                continue
            inspect_value(response, task_id=row["id"])
        for row in conn.execute("SELECT id, task_id, event_json FROM task_events"):
            try:
                event = json.loads(row["event_json"])
            except json.JSONDecodeError:
                continue
            inspect_value(event, task_id=row["task_id"], event_id=int(row["id"]))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for issue in issues:
        key = (issue.get("fileId"), issue.get("taskId"), issue.get("eventId"))
        if key not in seen:
            seen.add(key)
            deduped.append(issue)
    return deduped


def scan_file_storage(store: Store, config: dict[str, Any]) -> dict[str, Any]:
    rows = store.list_file_attachments()
    verifications = [verify_file_attachment(store, config, row["id"]) for row in rows]
    local_count = sum(1 for row in rows if row.get("source") != "remote_url")
    remote_count = sum(1 for row in rows if row.get("source") == "remote_url")
    orphaned_bytes = find_orphaned_file_bytes(store, config)
    metadata_issues = find_orphaned_file_metadata(store, config)
    artifact_issues = find_file_artifact_reference_issues(store, config)
    status_counts: dict[str, int] = {}
    for item in verifications:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    return {
        "success": True,
        "localAttachmentCount": local_count,
        "remoteUrlMetadataCount": remote_count,
        "totalLocalBytesFromMetadata": sum(int(row.get("size_bytes") or 0) for row in rows if row.get("source") != "remote_url"),
        "storageRootFileCount": len(_stored_byte_candidates(config)),
        "orphanedByteCount": len(orphaned_bytes),
        "missingByteMetadataCount": sum(1 for item in metadata_issues if item["status"] == "missing_bytes"),
        "checksumMismatchCount": sum(1 for item in metadata_issues if item["status"] == "checksum_mismatch"),
        "sizeMismatchCount": sum(1 for item in metadata_issues if item["status"] == "size_mismatch"),
        "unsafePathMetadataCount": sum(1 for item in metadata_issues if item["status"] == "unsafe_path"),
        "remoteUrlWithStoragePathCount": sum(1 for item in metadata_issues if item["status"] == "remote_url_with_local_path"),
        "artifactReferenceIssueCount": len(artifact_issues),
        "statusCounts": status_counts,
        "orphanedBytes": orphaned_bytes,
        "metadataIssues": metadata_issues,
        "artifactReferenceIssues": artifact_issues,
    }


def cleanup_orphaned_file_bytes(store: Store, config: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    paths = _orphaned_byte_paths(store, config)
    deleted: list[str] = []
    for path in paths:
        if dry_run:
            continue
        if _is_reparse_or_symlink(path):
            continue
        try:
            files.validate_storage_path(files.resolve_storage_root(config), path)
            path.unlink()
            deleted.append(_relative_storage_key(config, path))
        except (OSError, files.FileAttachmentError):
            continue
        for parent in path.parents:
            if parent == files.resolve_storage_root(config):
                break
            try:
                parent.rmdir()
            except OSError:
                break
    return {
        "success": True,
        "dryRun": dry_run,
        "orphanedByteCount": len(paths),
        "deletedByteCount": len(deleted),
        "deletedStorageKeys": deleted,
    }


def cleanup_missing_file_metadata(store: Store, config: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    candidates = [
        item
        for item in find_orphaned_file_metadata(store, config)
        if item["status"] == "missing_bytes" and item.get("source") != "remote_url"
    ]
    removed: list[str] = []
    if not dry_run:
        for item in candidates:
            if store.delete_file_attachment(item["fileId"]):
                removed.append(item["fileId"])
    return {
        "success": True,
        "dryRun": dry_run,
        "missingMetadataCount": len(candidates),
        "removedMetadataCount": len(removed),
        "removedFileIds": removed,
    }


def repair_file_storage(store: Store, config: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    metadata_result = cleanup_missing_file_metadata(store, config, dry_run=dry_run)
    scan = scan_file_storage(store, config)
    return {
        "success": True,
        "dryRun": dry_run,
        "metadataRepair": metadata_result,
        "remainingIssues": {
            "orphanedByteCount": scan["orphanedByteCount"],
            "metadataIssueCount": len(scan["metadataIssues"]),
            "artifactReferenceIssueCount": scan["artifactReferenceIssueCount"],
        },
    }


def delete_file(store: Store, config: dict[str, Any], file_id: str, *, delete_bytes: bool = False) -> dict[str, Any]:
    row = store.get_file_attachment(file_id)
    if row is None:
        raise FileOperationError("File attachment metadata was not found", "file_not_found")
    storage_path = row.get("storage_path")
    bytes_present = bool(storage_path and Path(storage_path).is_file())
    if bytes_present and not delete_bytes:
        raise FileOperationError(
            "Refusing metadata-only delete while stored bytes still exist; rerun with --delete-bytes.",
            "delete_bytes_required",
        )
    bytes_deleted = False
    if delete_bytes:
        try:
            bytes_deleted = files.delete_attachment_file(files.resolve_storage_root(config), file_id, config)
        except files.FileAttachmentError as exc:
            raise FileOperationError(str(exc), _file_error_code(exc)) from exc
    metadata_deleted = store.delete_file_attachment(file_id)
    return {
        "success": True,
        "fileId": file_id,
        "metadataDeleted": metadata_deleted,
        "bytesDeleted": bytes_deleted,
        "bytesWerePresent": bytes_present,
    }


def attach_file_artifact(
    store: Store,
    config: dict[str, Any],
    file_id: str,
    task_id: str,
    *,
    artifact_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    row = store.get_file_attachment(file_id)
    if row is None:
        raise FileOperationError("File attachment metadata was not found", "file_not_found")
    task = store.get_task(task_id)
    if task is None:
        raise FileOperationError("Task was not found", "task_not_found")
    artifact = files.build_file_artifact(
        row,
        artifact_id=artifact_id,
        name=name,
        public_url=config.get("server", {}).get("public_url"),
    )
    updated = store.append_task_artifact(task_id, artifact)
    if updated is None:
        raise FileOperationError("Task was not found", "task_not_found")
    event = {
        "artifactUpdate": {
            "taskId": task_id,
            "contextId": updated.context_id or "",
            "artifact": artifact,
            "append": False,
            "lastChunk": True,
            "metadata": {"final": False, "source": "local_file_attachment"},
        }
    }
    event_id = store.add_task_event(task_id, event)
    return {
        "success": True,
        "taskId": task_id,
        "artifact": artifact,
        "eventId": event_id,
    }


def file_stats(store: Store, config: dict[str, Any]) -> dict[str, Any]:
    stats = store.file_storage_stats()
    files_config = dict(config.get("files", {}))
    rows = store.list_file_attachments()
    local_count = sum(1 for row in rows if row.get("source") != "remote_url")
    remote_count = sum(1 for row in rows if row.get("source") == "remote_url")
    orphaned_count = len(_orphaned_byte_paths(store, config))
    missing_count = sum(
        1
        for row in rows
        if row.get("source") != "remote_url"
        and verify_file_attachment(store, config, row["id"])["status"] == "missing_bytes"
    )
    return {
        "success": True,
        "file_attachment_count": stats["file_attachment_count"],
        "file_attachment_bytes": stats["file_attachment_bytes"],
        "local_attachment_count": local_count,
        "remote_url_metadata_count": remote_count,
        "total_local_bytes_from_metadata": sum(
            int(row.get("size_bytes") or 0) for row in rows if row.get("source") != "remote_url"
        ),
        "storage_root_file_count": len(_stored_byte_candidates(config)),
        "orphaned_byte_count": orphaned_count,
        "missing_byte_metadata_count": missing_count,
        "max_total_storage_bytes": int(files_config.get("max_total_storage_bytes", 1073741824)),
    }


def prune_events(store: Store, config: dict[str, Any]) -> dict[str, int]:
    retention = config.get("retention", {})
    result = store.prune_task_events(
        max_events_per_task=retention.get("max_events_per_task"),
        older_than_days=retention.get("max_event_age_days"),
    )
    if config.get("sqlite", {}).get("maintenance_vacuum", False):
        store.vacuum()
        result["vacuumed"] = True
    else:
        result["vacuumed"] = False
    return result


def _target_state(config: dict[str, Any], section: str, key: str, default: TaskState) -> TaskState:
    try:
        target = TaskState(config.get(section, {}).get(key, default.value))
    except ValueError as exc:
        raise ValueError(f"{section}.{key} is not a valid task state") from exc
    if target not in RECOVERY_TARGETS:
        raise ValueError(f"{section}.{key} must be FAILED, CANCELED, or REJECTED")
    return target


def recover_expired_leases(store: Store, config: dict[str, Any]) -> dict[str, Any]:
    target = _target_state(config, "ownership", "expired_lease_state", TaskState.FAILED)
    return store.recover_expired_leases(target)


def recover_stale(
    store: Store, config: dict[str, Any], *, include_expired_leases: bool = True,
) -> dict[str, Any]:
    recovery = config.get("recovery", {})
    target = _target_state(config, "recovery", "stale_working_state", TaskState.FAILED)
    lease_result = (
        recover_expired_leases(store, config)
        if include_expired_leases
        else {"recovered_count": 0, "skipped_count": 0, "recovered_task_ids": []}
    )
    stale_result = store.recover_stale_tasks(
        RECOVERABLE_STATES,
        int(recovery.get("stale_task_after_seconds", 900)),
        target,
    )
    return {
        "recovered_count": lease_result["recovered_count"] + stale_result["recovered_count"],
        "skipped_count": lease_result["skipped_count"] + stale_result["skipped_count"],
        "recovered_task_ids": lease_result["recovered_task_ids"] + stale_result["recovered_task_ids"],
        "expired_lease_recovery": lease_result,
        "time_based_recovery": stale_result,
    }


def list_leases(store: Store, config: dict[str, Any]) -> dict[str, Any]:
    warning = config.get("observability", {}).get("lease_warning_seconds", 20)
    leases = store.lease_diagnostics(warning)
    return {
        "lease_count": len(leases),
        "active_lease_count": sum(1 for lease in leases if not lease["expired"]),
        "expired_lease_count": sum(1 for lease in leases if lease["expired"]),
        "stale_heartbeat_count": sum(1 for lease in leases if lease["heartbeat_stale"]),
        "leases": leases,
    }


def list_cancellations(store: Store) -> dict[str, Any]:
    store.expire_cancellation_requests()
    requests = [
        {key: value for key, value in item.items() if key != "reason"}
        for item in store.list_cancellation_requests()
    ]
    return {
        "cancellation_count": len(requests),
        "pending_cancellation_count": sum(1 for item in requests if item["status"] == "pending"),
        "expired_cancellation_count": sum(1 for item in requests if item["status"] == "expired"),
        "cancellations": requests,
    }


def expire_cancellations(store: Store) -> dict[str, Any]:
    return store.expire_cancellation_requests()


def maintenance_stats(store: Store, config: dict[str, Any]) -> dict[str, Any]:
    store.expire_cancellation_requests()
    observability = dict(config.get("observability", {}))
    warning = observability.get("lease_warning_seconds", 20)
    stats = store.maintenance_stats(warning)
    result = {
        **stats,
        "retention": dict(config.get("retention", {})),
        "recovery": dict(config.get("recovery", {})),
        "ownership": {
            **dict(config.get("ownership", {})),
            "lease_count": stats["lease_count"],
            "expired_lease_count": stats["expired_lease_count"],
            "active_lease_count": stats["active_lease_count"],
            "stale_heartbeat_count": stats["stale_heartbeat_count"],
        },
        "cancellation": {
            **dict(config.get("cancellation", {})),
            "pending_cancellation_count": stats["pending_cancellation_count"],
            "expired_cancellation_count": stats["expired_cancellation_count"],
        },
        "files": {
            **dict(config.get("files", {})),
            "file_attachment_count": stats["file_attachment_count"],
            "file_attachment_bytes": stats["file_attachment_bytes"],
        },
        "observability": observability,
        "faults": dict(config.get("faults", {})),
        "sqlite": {
            key: stats[key] for key in (
                "database_path", "database_size_bytes", "journal_mode", "busy_timeout_ms",
                "synchronous", "sqlite_warnings", "sqlite_warning_count",
                "sqlite_retry_count", "sqlite_retry_exhausted_count",
            )
        },
        "event_bounds": {
            key: stats[key] for key in (
                "oldest_event_id", "newest_event_id",
                "oldest_event_created_at", "newest_event_created_at",
            )
        },
    }
    if observability.get("include_diagnostics_in_stats", True):
        result["lease_diagnostics"] = store.lease_diagnostics(warning)
    return result
