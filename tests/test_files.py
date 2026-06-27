import json
import os
import sqlite3
from pathlib import Path

import pytest

from hermes_a2a_bridge import files
from hermes_a2a_bridge.operations import (
    FileOperationError,
    add_remote_url_file_reference,
    cleanup_missing_file_metadata,
    cleanup_orphaned_file_bytes,
    find_file_artifact_reference_issues,
    ingest_local_file,
    repair_file_storage,
    scan_file_storage,
    verify_file_attachment,
)
from hermes_a2a_bridge.store import Store


def test_filename_sanitization_handles_traversal_and_windows_names():
    assert files.sanitize_filename("../../secret.txt") == "secret.txt"
    assert files.sanitize_filename("..\\..\\secret.txt") == "secret.txt"
    assert files.sanitize_filename("CON") == "_CON"
    assert files.sanitize_filename("report:final?.pdf") == "report_final_.pdf"


def test_storage_path_validation_rejects_traversal_and_windows_separators(tmp_path):
    root = tmp_path / "storage"
    files.ensure_storage_root(root)
    assert files.validate_storage_path(root, root / "aa" / "file_abc" / "content").is_relative_to(root.resolve())
    with pytest.raises(files.FileAttachmentError, match="escapes"):
        files.validate_storage_path(root, root / ".." / "outside")
    with pytest.raises(files.FileAttachmentError, match="escapes"):
        files.validate_storage_path(root, Path(str(root) + "\\..\\outside"))


def test_symlink_storage_escape_is_rejected_if_supported(tmp_path):
    root = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    files.ensure_storage_root(root)
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")
    with pytest.raises(files.FileAttachmentError, match="escapes"):
        files.validate_storage_path(root, link / "content")


def test_atomic_write_sha_and_delete_stay_under_storage_root(tmp_path, config):
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    root = files.resolve_storage_root(config)
    file_id = "file_abcdefghijklmnopqrstuv"
    result = files.write_attachment_bytes_atomic(root, file_id, b"hello", config)
    written = Path(result["storage_path"])
    assert written.read_bytes() == b"hello"
    assert written.is_relative_to(root)
    assert result["size_bytes"] == 5
    assert result["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert files.sha256_file(written) == result["sha256"]
    assert files.delete_attachment_file(root, file_id, config) is True
    assert files.delete_attachment_file(root, file_id, config) is False


def test_atomic_write_cleans_partial_file_on_replace_failure(tmp_path, config, monkeypatch):
    root = tmp_path / "storage"
    file_id = "file_abcdefghijklmnopqrstuv"

    def fail_replace(source, target):
        raise OSError("boom SECRET_TOKEN")

    monkeypatch.setattr(files.os, "replace", fail_replace)
    with pytest.raises(files.FileAttachmentError) as excinfo:
        files.write_attachment_bytes_atomic(root, file_id, b"hello", config)
    assert "SECRET_TOKEN" not in str(excinfo.value)
    assert not list(root.rglob(".tmp-*"))


def test_size_and_mime_validation(config):
    config["files"]["max_file_bytes"] = 4
    with pytest.raises(files.FileAttachmentError, match="size"):
        files.validate_file_size(5, config)
    assert files.guess_mime_type("report.pdf") == "application/pdf"
    assert files.guess_mime_type("ignored.bin", "text/plain; charset=utf-8") == "text/plain"
    files.validate_mime_type("text/plain", config)
    with pytest.raises(files.FileAttachmentError, match="not allowed"):
        files.validate_mime_type("application/x-msdownload", config)
    with pytest.raises(files.FileAttachmentError, match="Unknown"):
        files.validate_mime_type(None, config)
    config["files"]["reject_unknown_mime"] = False
    files.validate_mime_type(None, config)


def test_remote_url_validation_and_sanitization(config):
    files.validate_remote_url_reference("https://example.test/report.pdf?token=secret#frag", config)
    assert files.is_supported_remote_url("https://example.test/report.pdf")
    assert files.sanitize_source_url("https://user:pass@example.test/report.pdf?token=secret#frag") == (
        "https://example.test/report.pdf"
    )
    assert files.sanitize_source_url("https://example.test/secret-token/report.pdf") is None
    for value in (
        "file:///tmp/report.pdf",
        "ftp://example.test/report.pdf",
        "data:text/plain,hello",
        "javascript:alert(1)",
        r"C:\Users\asimo\report.pdf",
        r"\\server\share\report.pdf",
        "report.pdf",
    ):
        with pytest.raises(files.FileAttachmentError):
            files.validate_remote_url_reference(value, config)


def test_add_remote_url_reference_is_metadata_only_and_does_not_touch_network(tmp_path, config, monkeypatch):
    store = Store(tmp_path / "remote.sqlite3")

    def forbidden_network(*args, **kwargs):
        raise AssertionError("metadata-only URL references must not open network connections")

    monkeypatch.setattr("socket.create_connection", forbidden_network)
    result = add_remote_url_file_reference(
        "https://user:pass@example.test/report.pdf?token=secret#frag",
        store,
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
        size_bytes=123,
        sha256="A" * 64,
        metadata={"purpose": "remote"},
    )

    public = result["file"]
    row = store.get_file_attachment(public["fileId"])
    assert public["source"] == "remote_url"
    assert public["metadataOnly"] is True
    assert public["bytesAvailable"] is False
    assert public["sourceUrl"] == "https://example.test/report.pdf"
    assert public["sha256"] == "a" * 64
    assert row["storage_path"] is None
    serialized = str(public)
    assert "user:pass" not in serialized
    assert "token=secret" not in serialized
    assert "#frag" not in serialized


def test_add_remote_url_reference_rejects_config_disabled_mime_size_and_sha(tmp_path, config):
    store = Store(tmp_path / "remote-errors.sqlite3")
    config["files"]["allow_remote_url_references"] = False
    with pytest.raises(FileOperationError) as disabled:
        add_remote_url_file_reference("https://example.test/report.pdf", store, config, declared_mime_type="application/pdf")
    assert disabled.value.code == "remote_url_references_disabled"

    config["files"]["allow_remote_url_references"] = True
    with pytest.raises(FileOperationError) as bad_mime:
        add_remote_url_file_reference("https://example.test/report.exe", store, config, declared_mime_type="application/x-msdownload")
    assert bad_mime.value.code == "mime_type_not_allowed"
    with pytest.raises(FileOperationError) as bad_size:
        add_remote_url_file_reference("https://example.test/report.pdf", store, config, declared_mime_type="application/pdf", size_bytes=-1)
    assert bad_size.value.code == "file_too_large"
    with pytest.raises(FileOperationError) as bad_sha:
        add_remote_url_file_reference("https://example.test/report.pdf", store, config, declared_mime_type="application/pdf", sha256="bad")
    assert bad_sha.value.code == "invalid_sha256"


def test_content_disposition_is_attachment_and_safe():
    header = files.safe_content_disposition('../../"report final".pdf')
    assert header.startswith("attachment;")
    assert "report_final_.pdf" in header
    assert "../" not in header
    assert "\r" not in header
    assert "\n" not in header


def test_invalid_file_ids_are_rejected(tmp_path, config):
    with pytest.raises(files.FileAttachmentError, match="Invalid"):
        files.attachment_storage_path(tmp_path, "../bad", 2)
    with pytest.raises(files.FileAttachmentError, match="Invalid"):
        files.write_attachment_bytes_atomic(tmp_path, "file_short", b"x", config)


def test_local_ingest_stores_bytes_metadata_and_safe_public_result(tmp_path, config):
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    store = Store(tmp_path / "tasks.sqlite3")

    result = ingest_local_file(
        source,
        store,
        config,
        task_id="task-1",
        artifact_id="artifact-1",
        name="../unsafe name.txt",
        metadata={"purpose": "test"},
    )

    public = result["file"]
    assert result["success"] is True
    assert public["fileId"].startswith("file_")
    assert public["name"] == "unsafe_name.txt"
    assert public["mimeType"] == "text/plain"
    assert public["sizeBytes"] == 5
    assert public["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert public["metadata"] == {"purpose": "test"}
    assert "storage_path" not in public
    row = store.get_file_attachment(public["fileId"])
    written = Path(row["storage_path"])
    assert written.read_text(encoding="utf-8") == "hello"
    assert written.is_relative_to(files.resolve_storage_root(config))


def test_file_attachment_artifact_part_is_safe_and_uses_local_public_url(tmp_path):
    row = {
        "id": "file_abcdefghijklmnopqrstuv",
        "filename": r"C:\Users\asimo\secret\report.pdf",
        "safe_filename": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12345,
        "sha256": "a" * 64,
        "storage_path": str(tmp_path / "storage" / "content"),
        "source": "remote_url_reference",
        "source_url": "https://user:pass@example.test/report.pdf?token=secret",
        "created_at": "2026-06-25T00:00:00+00:00",
        "metadata": {"purpose": "test"},
    }

    part = files.file_attachment_to_artifact_part(row, "http://127.0.0.1:8765")
    assert part == {
        "file": {
            "fileId": "file_abcdefghijklmnopqrstuv",
            "name": "report.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": 12345,
            "sha256": "a" * 64,
            "uri": "http://127.0.0.1:8765/files/file_abcdefghijklmnopqrstuv",
        },
        "metadata": {"purpose": "test"},
    }
    serialized = str(part)
    assert "storage_path" not in serialized
    assert str(tmp_path) not in serialized
    assert "secret" not in serialized
    assert "sourceUrl" not in serialized


def test_remote_url_artifact_part_is_metadata_only_and_omits_byte_uri():
    row = {
        "id": "file_abcdefghijklmnopqrstuv",
        "filename": "report.pdf",
        "safe_filename": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": None,
        "sha256": None,
        "storage_path": None,
        "source": "remote_url",
        "source_url": "https://example.test/report.pdf?token=secret",
        "created_at": "2026-06-25T00:00:00+00:00",
        "metadata": {"purpose": "remote"},
    }
    part = files.file_attachment_to_artifact_part(row, "http://127.0.0.1:8765")
    assert part == {
        "file": {
            "fileId": "file_abcdefghijklmnopqrstuv",
            "name": "report.pdf",
            "mimeType": "application/pdf",
            "metadataOnly": True,
            "bytesAvailable": False,
            "sourceUrl": "https://example.test/report.pdf",
        },
        "metadata": {"purpose": "remote"},
    }
    serialized = json.dumps(part)
    assert "/files/file_" not in serialized
    assert "token=secret" not in serialized


def test_file_artifact_omits_uri_for_unsafe_public_url(tmp_path):
    row = {
        "id": "file_abcdefghijklmnopqrstuv",
        "filename": "report.txt",
        "safe_filename": "report.txt",
        "mime_type": "text/plain",
        "size_bytes": 5,
        "sha256": "b" * 64,
        "storage_path": str(tmp_path / "content"),
        "source": "local_cli",
        "created_at": "2026-06-25T00:00:00+00:00",
        "metadata": {},
    }
    artifact = files.build_file_artifact(
        row,
        artifact_id="artifact-1",
        name="result-file",
        public_url="https://user:token@example.test/base?token=secret",
    )
    assert artifact["artifactId"] == "artifact-1"
    assert artifact["name"] == "result-file"
    assert "uri" not in artifact["parts"][0]["file"]


def test_local_ingest_rejects_missing_directory_oversized_mime_and_unknown(tmp_path, config):
    store = Store(tmp_path / "tasks.sqlite3")
    with pytest.raises(FileOperationError) as missing:
        ingest_local_file(tmp_path / "missing.txt", store, config)
    assert missing.value.code == "local_file_not_found"

    with pytest.raises(FileOperationError) as directory:
        ingest_local_file(tmp_path, store, config)
    assert directory.value.code == "local_file_not_regular"

    source = tmp_path / "big.txt"
    source.write_text("hello", encoding="utf-8")
    config["files"]["max_file_bytes"] = 4
    with pytest.raises(FileOperationError) as oversized:
        ingest_local_file(source, store, config)
    assert oversized.value.code == "file_too_large"

    config["files"]["max_file_bytes"] = 100
    with pytest.raises(FileOperationError) as disallowed:
        ingest_local_file(source, store, config, declared_mime_type="application/x-msdownload")
    assert disallowed.value.code == "mime_type_not_allowed"

    unknown = tmp_path / "blob.unknownext"
    unknown.write_bytes(b"x")
    with pytest.raises(FileOperationError) as unknown_error:
        ingest_local_file(unknown, store, config)
    assert unknown_error.value.code == "unknown_mime_type"


def test_local_ingest_enforces_metadata_quota_before_writing(tmp_path, config):
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    config["files"]["max_total_storage_bytes"] = 5
    source = tmp_path / "a.txt"
    source.write_text("abcd", encoding="utf-8")
    store = Store(tmp_path / "tasks.sqlite3")
    assert ingest_local_file(source, store, config)["success"] is True

    second = tmp_path / "b.txt"
    second.write_text("ef", encoding="utf-8")
    before = sorted(path.as_posix() for path in files.resolve_storage_root(config).rglob("*") if path.is_file())
    with pytest.raises(FileOperationError) as quota:
        ingest_local_file(second, store, config)
    after = sorted(path.as_posix() for path in files.resolve_storage_root(config).rglob("*") if path.is_file())
    assert quota.value.code == "storage_quota_exceeded"
    assert after == before


def test_local_ingest_rolls_back_bytes_when_metadata_insert_fails(tmp_path, config, monkeypatch):
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    store = Store(tmp_path / "tasks.sqlite3")

    def fail_insert(**kwargs):
        raise RuntimeError("database failed with test-secret-token")

    monkeypatch.setattr(store, "add_file_attachment", fail_insert)
    with pytest.raises(FileOperationError) as failed:
        ingest_local_file(source, store, config)
    assert "test-secret-token" not in str(failed.value)
    assert not [path for path in files.resolve_storage_root(config).rglob("content") if path.is_file()]


def _ingest_for_maintenance(tmp_path, config, store, content=b"hello"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    source = tmp_path / "report.txt"
    source.write_bytes(content)
    result = ingest_local_file(source, store, config)
    return result["file"]["fileId"], store.get_file_attachment(result["file"]["fileId"])


def test_verify_file_attachment_statuses_are_safe(tmp_path, config):
    store = Store(tmp_path / "verify.sqlite3")
    file_id, row = _ingest_for_maintenance(tmp_path, config, store)

    ok = verify_file_attachment(store, config, file_id)
    assert ok["status"] == "ok"
    assert ok["bytesAvailable"] is True
    assert "storage_path" not in json.dumps(ok)
    assert str(tmp_path) not in json.dumps(ok)

    unknown = verify_file_attachment(store, config, "file_unknownabcdefghijklmnop")
    assert unknown["success"] is False
    assert unknown["status"] == "file_not_found"

    remote_id = add_remote_url_file_reference(
        "https://example.test/report.pdf",
        store,
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
    )["file"]["fileId"]
    remote = verify_file_attachment(store, config, remote_id)
    assert remote["status"] == "metadata_only"
    assert remote["metadataOnly"] is True
    assert remote["bytesAvailable"] is False

    Path(row["storage_path"]).unlink()
    missing = verify_file_attachment(store, config, file_id)
    assert missing["status"] == "missing_bytes"
    assert "storage_path" not in json.dumps(missing)


def test_verify_reports_checksum_size_and_unsafe_path(tmp_path, config):
    store = Store(tmp_path / "verify-bad.sqlite3")
    checksum_id, checksum_row = _ingest_for_maintenance(tmp_path / "checksum", config, store, b"abcde")
    Path(checksum_row["storage_path"]).write_bytes(b"xxxxx")
    assert verify_file_attachment(store, config, checksum_id)["status"] == "checksum_mismatch"

    size_id, size_row = _ingest_for_maintenance(tmp_path / "size", config, store, b"abcde")
    Path(size_row["storage_path"]).write_bytes(b"too-long")
    size = verify_file_attachment(store, config, size_id)
    assert size["status"] == "size_mismatch"
    assert size["actualSizeBytes"] == 8

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    unsafe_id = "file_unsafeabcdefghijklmnopq"
    store.add_file_attachment(
        file_id=unsafe_id,
        filename="outside.txt",
        safe_filename="outside.txt",
        mime_type="text/plain",
        size_bytes=7,
        sha256=files.sha256_file(outside),
        storage_path=str(outside),
        source="local_cli",
    )
    unsafe = verify_file_attachment(store, config, unsafe_id)
    assert unsafe["status"] == "unsafe_path"
    assert "outside.txt" not in json.dumps(unsafe)


def test_scan_reports_orphans_missing_remote_counts_and_artifact_issues(tmp_path, config):
    store = Store(tmp_path / "scan.sqlite3")
    file_id, row = _ingest_for_maintenance(tmp_path, config, store)
    Path(row["storage_path"]).unlink()
    root = files.resolve_storage_root(config)
    orphan = root / "aa" / "orphan" / "content"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    remote_id = add_remote_url_file_reference(
        "https://example.test/report.pdf",
        store,
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
    )["file"]["fileId"]
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE file_attachments SET storage_path=? WHERE id=?",
            (str(root / "should-not-exist"), remote_id),
        )
    missing_ref = "file_missingabcdefghijklmn"
    store.add_task_event("task-1", {"artifactUpdate": {"artifact": {"parts": [{"file": {"fileId": missing_ref}}]}}})

    scan = scan_file_storage(store, config)
    assert scan["localAttachmentCount"] == 1
    assert scan["remoteUrlMetadataCount"] == 1
    assert scan["orphanedByteCount"] == 1
    assert scan["missingByteMetadataCount"] == 1
    assert scan["remoteUrlWithStoragePathCount"] == 1
    assert scan["artifactReferenceIssueCount"] == 1
    assert scan["metadataIssues"][0]["fileId"] in {file_id, remote_id}
    serialized = json.dumps(scan)
    assert str(tmp_path) not in serialized
    assert "storage_path" not in serialized


def test_scan_does_not_follow_symlink_or_delete_anything(tmp_path, config):
    store = Store(tmp_path / "symlink-scan.sqlite3")
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    root = files.resolve_storage_root(config)
    files.ensure_storage_root(root)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")
    scan = scan_file_storage(store, config)
    assert scan["storageRootFileCount"] == 0
    assert outside.exists()


def test_cleanup_orphans_dry_run_and_confirm_only_delete_controlled_orphans(tmp_path, config):
    store = Store(tmp_path / "cleanup.sqlite3")
    config["files"]["storage_dir"] = str(tmp_path / "storage")
    root = files.resolve_storage_root(config)
    orphan = root / "aa" / "orphan" / "content"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    dry = cleanup_orphaned_file_bytes(store, config, dry_run=True)
    assert dry["orphanedByteCount"] == 1
    assert dry["deletedByteCount"] == 0
    assert orphan.exists()
    assert outside.exists()

    confirmed = cleanup_orphaned_file_bytes(store, config, dry_run=False)
    assert confirmed["deletedByteCount"] == 1
    assert not orphan.exists()
    assert outside.exists()
    assert str(tmp_path) not in json.dumps(confirmed)


def test_repair_removes_missing_local_metadata_but_preserves_remote_and_other_tables(tmp_path, config):
    store = Store(tmp_path / "repair.sqlite3")
    file_id, row = _ingest_for_maintenance(tmp_path, config, store)
    Path(row["storage_path"]).unlink()
    remote_id = add_remote_url_file_reference(
        "https://example.test/report.pdf",
        store,
        config,
        name="report.pdf",
        declared_mime_type="application/pdf",
    )["file"]["fileId"]
    store.registry_add("demo", "http://demo.test", "secret")
    store.add_task_event("task-1", {"n": 1})
    before_events = store.count_task_events()

    dry = repair_file_storage(store, config, dry_run=True)
    assert dry["metadataRepair"]["missingMetadataCount"] == 1
    assert store.get_file_attachment(file_id) is not None

    confirmed = repair_file_storage(store, config, dry_run=False)
    assert confirmed["metadataRepair"]["removedFileIds"] == [file_id]
    assert store.get_file_attachment(file_id) is None
    assert store.get_file_attachment(remote_id) is not None
    assert store.registry_get("demo") is not None
    assert store.count_task_events() == before_events

    second = cleanup_missing_file_metadata(store, config, dry_run=False)
    assert second["removedMetadataCount"] == 0


def test_artifact_reference_issue_detection_reports_missing_file_ids(tmp_path, config):
    store = Store(tmp_path / "artifact-issues.sqlite3")
    missing_ref = "file_missingabcdefghijklmn"
    store.add_task_event("task-1", {"artifactUpdate": {"artifact": {"parts": [{"file": {"fileId": missing_ref}}]}}})
    issues = find_file_artifact_reference_issues(store, config)
    assert issues == [{"fileId": missing_ref, "status": "missing_file_metadata", "taskId": "task-1", "eventId": 1}]
