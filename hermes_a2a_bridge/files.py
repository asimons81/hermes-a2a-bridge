"""Safe local attachment storage helpers.

This module contains controlled local attachment storage helpers. Runtime file
parts and executor path exposure remain disabled.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

FILE_ID_RE = re.compile(r"^file_[A-Za-z0-9_-]{22,}$")
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
TOKEN_LIKE_RE = re.compile(r"(token|secret|signature|sig|auth|key|password|passwd|credential|session)", re.I)
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class FileAttachmentError(ValueError):
    """Raised for controlled file attachment validation failures."""


def _files_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("files", config)


def generate_file_id() -> str:
    return f"file_{secrets.token_urlsafe(24)}"


def sanitize_filename(filename: str) -> str:
    value = str(filename or "").replace("\\", "/").split("/")[-1]
    value = value.replace("\x00", "").strip().strip(". ")
    value = value.replace(":", "_")
    value = SAFE_FILENAME_RE.sub("_", value)
    value = value.strip("._ ")
    if not value:
        value = "attachment"
    stem = value.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        value = f"_{value}"
    return value[:180] or "attachment"


def safe_content_disposition(filename: str) -> str:
    safe = sanitize_filename(filename)
    ascii_name = safe.encode("ascii", "ignore").decode("ascii") or "attachment"
    escaped = ascii_name.replace("\\", "_").replace('"', "_")
    encoded = quote(safe, safe="")
    return f'attachment; filename="{escaped}"; filename*=UTF-8\'\'{encoded}'


def resolve_storage_root(config: dict[str, Any]) -> Path:
    files = _files_config(config)
    return Path(str(files.get("storage_dir", "~/.hermes/a2a/files"))).expanduser().resolve()


def _validate_file_id(file_id: str) -> None:
    if not FILE_ID_RE.fullmatch(file_id):
        raise FileAttachmentError("Invalid file attachment id")


def attachment_storage_path(storage_root: Path | str, file_id: str, shard_depth: int = 2) -> Path:
    _validate_file_id(file_id)
    depth = max(0, int(shard_depth))
    suffix = file_id.removeprefix("file_")
    shards = [suffix[index:index + 2] for index in range(0, depth * 2, 2)]
    return Path(storage_root).joinpath(*shards, file_id, "content")


def ensure_storage_root(storage_root: Path | str) -> None:
    root = Path(storage_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve()
    if not resolved.is_dir():
        raise FileAttachmentError("Attachment storage root is not a directory")
    if _has_reparse_point(resolved):
        raise FileAttachmentError("Attachment storage root must not be a symlink or reparse point")


def _has_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attrs = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def validate_storage_path(storage_root: Path | str, candidate_path: Path | str) -> Path:
    root = Path(storage_root).expanduser().resolve()
    candidate = Path(candidate_path).expanduser()
    resolved_parent = candidate.parent.resolve()
    if _has_reparse_point(root) or any(_has_reparse_point(parent) for parent in (resolved_parent, *resolved_parent.parents)):
        try:
            resolved_parent.relative_to(root)
        except ValueError:
            pass
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise FileAttachmentError("Attachment storage path escapes the storage root") from exc
    return resolved


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_local_ingest_path(path: Path | str) -> Path:
    source = Path(path).expanduser()
    try:
        stat = source.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise FileAttachmentError("Local attachment path does not exist") from exc
    except OSError as exc:
        raise FileAttachmentError("Local attachment path cannot be inspected") from exc
    if _has_reparse_point(source):
        raise FileAttachmentError("Local attachment path must not be a symlink or reparse point")
    if not source.is_file():
        raise FileAttachmentError("Local attachment path must be a regular file")
    if int(stat.st_size) < 0:
        raise FileAttachmentError("Local attachment size is invalid")
    return source.resolve(strict=True)


def guess_mime_type(filename: str, declared_mime_type: str | None = None) -> str | None:
    if declared_mime_type:
        return declared_mime_type.split(";", 1)[0].strip().lower() or None
    guessed, _ = mimetypes.guess_type(sanitize_filename(filename))
    return guessed


def validate_mime_type(mime_type: str | None, config: dict[str, Any]) -> None:
    files = _files_config(config)
    if not mime_type:
        if files.get("reject_unknown_mime", True):
            raise FileAttachmentError("Unknown attachment MIME type is not allowed")
        return
    allowed = set(files.get("allowed_mime_types", ()))
    if allowed and mime_type not in allowed:
        raise FileAttachmentError("Attachment MIME type is not allowed")


def validate_file_size(size_bytes: int, config: dict[str, Any]) -> None:
    max_file_bytes = int(_files_config(config).get("max_file_bytes", 10485760))
    if int(size_bytes) < 0:
        raise FileAttachmentError("Attachment size must be non-negative")
    if int(size_bytes) > max_file_bytes:
        raise FileAttachmentError("Attachment exceeds the configured file size limit")


def validate_sha256(value: str | None) -> None:
    if value is not None and not SHA256_RE.fullmatch(str(value)):
        raise FileAttachmentError("Attachment SHA-256 must be 64 hexadecimal characters")


def is_supported_remote_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    value = url.strip()
    if not value or value.startswith(("\\\\", "//")):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc) and bool(parts.hostname)


def sanitize_source_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    hostname = parts.hostname
    path = parts.path or ""
    if TOKEN_LIKE_RE.search(hostname) or TOKEN_LIKE_RE.search(path):
        return None
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def validate_remote_url_reference(url: str, config: dict[str, Any]) -> None:
    files = _files_config(config)
    if not files.get("allow_remote_url_references", True):
        raise FileAttachmentError("Remote URL file references are disabled by configuration")
    if files.get("auto_fetch_remote_urls", False):
        raise FileAttachmentError(
            "Remote URL auto-fetch is not implemented; metadata-only URL references do not fetch bytes"
        )
    if not is_supported_remote_url(url):
        raise FileAttachmentError("Remote URL file references must use an absolute HTTP(S) URL")


def _storage_bytes(storage_root: Path) -> int:
    if not storage_root.exists():
        return 0
    total = 0
    for path in storage_root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def write_attachment_bytes_atomic(
    storage_root: Path | str,
    file_id: str,
    data: bytes,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)):
        raise FileAttachmentError("Attachment data must be bytes")
    files = _files_config(config)
    validate_file_size(len(data), files)
    root = Path(storage_root).expanduser().resolve()
    ensure_storage_root(root)
    target = validate_storage_path(
        root,
        attachment_storage_path(root, file_id, int(files.get("shard_depth", 2))),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target = validate_storage_path(root, target)
    if _has_reparse_point(target.parent):
        raise FileAttachmentError("Attachment storage directory must not be a symlink or reparse point")
    quota = int(files.get("max_total_storage_bytes", 1073741824))
    if _storage_bytes(root) + len(data) > quota:
        raise FileAttachmentError("Attachment storage quota would be exceeded")
    temp_path: Path | None = None
    digest = hashlib.sha256()
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=target.parent, prefix=".tmp-") as handle:
            temp_path = Path(handle.name)
            digest.update(data)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        validate_storage_path(root, temp_path)
        if _storage_bytes(root) > quota:
            raise FileAttachmentError("Attachment storage quota would be exceeded")
        os.replace(temp_path, target)
        temp_path = None
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, FileAttachmentError):
            raise
        raise FileAttachmentError("Attachment write failed") from exc
    return {
        "file_id": file_id,
        "storage_path": str(target),
        "size_bytes": len(data),
        "sha256": digest.hexdigest(),
    }


def write_attachment_file_atomic(
    storage_root: Path | str,
    file_id: str,
    source_path: Path | str,
    config: dict[str, Any],
) -> dict[str, Any]:
    source = validate_local_ingest_path(source_path)
    files = _files_config(config)
    source_size = source.stat(follow_symlinks=False).st_size
    validate_file_size(source_size, files)
    root = Path(storage_root).expanduser().resolve()
    ensure_storage_root(root)
    target = validate_storage_path(
        root,
        attachment_storage_path(root, file_id, int(files.get("shard_depth", 2))),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target = validate_storage_path(root, target)
    if _has_reparse_point(target.parent):
        raise FileAttachmentError("Attachment storage directory must not be a symlink or reparse point")
    quota = int(files.get("max_total_storage_bytes", 1073741824))
    if _storage_bytes(root) + source_size > quota:
        raise FileAttachmentError("Attachment storage quota would be exceeded")
    temp_path: Path | None = None
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with source.open("rb") as input_handle:
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=target.parent, prefix=".tmp-") as handle:
                temp_path = Path(handle.name)
                for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                    size_bytes += len(chunk)
                    if size_bytes > int(files.get("max_file_bytes", 10485760)):
                        raise FileAttachmentError("Attachment exceeds the configured file size limit")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        validate_storage_path(root, temp_path)
        if _storage_bytes(root) > quota:
            raise FileAttachmentError("Attachment storage quota would be exceeded")
        os.replace(temp_path, target)
        temp_path = None
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, FileAttachmentError):
            raise
        raise FileAttachmentError("Attachment write failed") from exc
    return {
        "file_id": file_id,
        "storage_path": str(target),
        "size_bytes": size_bytes,
        "sha256": digest.hexdigest(),
    }


def delete_attachment_file(storage_root: Path | str, file_id: str, config: dict[str, Any]) -> bool:
    files = _files_config(config)
    root = Path(storage_root).expanduser().resolve()
    target = validate_storage_path(
        root,
        attachment_storage_path(root, file_id, int(files.get("shard_depth", 2))),
    )
    if not target.exists():
        return False
    if target.is_symlink():
        raise FileAttachmentError("Attachment target must not be a symlink")
    target.unlink()
    try:
        target.parent.rmdir()
    except OSError:
        pass
    return True


def public_file_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    if metadata is None:
        metadata = row.get("metadata_json") or {}
    if isinstance(metadata, str):
        import json

        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    result = {
        "fileId": row["id"],
        "name": row.get("safe_filename") or sanitize_filename(row.get("filename") or "attachment"),
        "mimeType": row.get("mime_type"),
        "sizeBytes": int(row["size_bytes"]) if row.get("size_bytes") is not None else None,
        "sha256": row.get("sha256"),
        "source": row.get("source"),
        "createdAt": row.get("created_at"),
        "metadata": metadata,
    }
    if row.get("source") == "remote_url":
        result["metadataOnly"] = True
        result["bytesAvailable"] = False
    source_url = sanitize_source_url(row.get("source_url"))
    if source_url:
        result["sourceUrl"] = source_url
    return {key: value for key, value in result.items() if value is not None}


def _safe_file_route_url(public_url: str | None, file_id: str) -> str | None:
    if not public_url:
        return None
    _validate_file_id(file_id)
    try:
        parts = urlsplit(str(public_url).rstrip("/"))
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    hostname = (parts.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return urlunsplit((parts.scheme, parts.netloc, f"{parts.path.rstrip('/')}/files/{file_id}", "", ""))


def file_attachment_to_artifact_part(row: dict[str, Any], public_url: str | None = None) -> dict[str, Any]:
    public = public_file_metadata(row)
    file_id = public["fileId"]
    file = {
        "fileId": file_id,
        "name": public["name"],
        "mimeType": public.get("mimeType"),
        "sizeBytes": public.get("sizeBytes"),
        "sha256": public.get("sha256"),
    }
    if public.get("metadataOnly"):
        file["metadataOnly"] = True
        file["bytesAvailable"] = False
        if public.get("sourceUrl"):
            file["sourceUrl"] = public["sourceUrl"]
    uri = _safe_file_route_url(public_url, file_id)
    if uri and not public.get("metadataOnly"):
        file["uri"] = uri
    file = {key: value for key, value in file.items() if value is not None}
    part = {"file": file}
    metadata = public.get("metadata")
    if isinstance(metadata, dict) and metadata:
        part["metadata"] = metadata
    return part


def build_file_artifact(
    row: dict[str, Any],
    artifact_id: str | None = None,
    name: str | None = None,
    public_url: str | None = None,
) -> dict[str, Any]:
    public = public_file_metadata(row)
    resolved_artifact_id = artifact_id or row.get("artifact_id") or f"artifact-{public['fileId']}"
    artifact = {
        "artifactId": str(resolved_artifact_id),
        "name": name or public["name"],
        "parts": [file_attachment_to_artifact_part(row, public_url)],
        "metadata": {"attachment": {"fileId": public["fileId"]}},
    }
    return artifact
