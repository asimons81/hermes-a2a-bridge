import pytest
import tomllib
from pathlib import Path

import hermes_a2a_bridge
from hermes_a2a_bridge.config import default_config, load_config
from hermes_a2a_bridge.errors import ConfigError


def test_config_creates_with_secure_token_and_local_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    data = load_config(path)
    assert path.exists()
    assert len(data["server"]["auth_token"]) >= 32
    assert data["server"]["host"] == "127.0.0.1"
    assert data["server"]["allow_remote_hosts"] is False
    assert data["retention"] == {
        "max_events_per_task": 500, "max_event_age_days": 30, "prune_on_startup": True,
    }
    assert data["recovery"]["stale_task_after_seconds"] == 900
    assert data["recovery"]["recover_on_startup"] is True
    assert data["streaming"]["poll_interval_seconds"] == 0.5
    assert data["parts"] == {
        "max_data_part_bytes": 65536,
        "allow_data_parts": True,
        "allow_file_parts": False,
        "allow_file_id_references": False,
        "allow_remote_url_file_references": False,
        "allow_inline_file_bytes": False,
    }
    assert data["files"] == {
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
    }
    assert data["artifacts"] == {
        "parse_json_output": True, "max_artifact_data_bytes": 65536,
    }


def test_existing_config_is_preserved(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("custom: true\n", encoding="utf-8")
    data = load_config(path)
    assert data["custom"] is True
    assert data["server"]["host"] == "127.0.0.1"
    assert "public_url_explicit" in data["server"]


def test_config_backfills_missing_fields_and_syncs_public_url(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  host: 127.0.0.1\n  port: 9999\n", encoding="utf-8")
    data = load_config(path)
    assert data["server"]["public_url"] == "http://127.0.0.1:9999"
    assert data["executor"]["command"] == ["hermes", "chat", "-q", "{prompt}"]


def test_operations_config_backfill_preserves_existing_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "retention:\n  max_events_per_task: 77\n"
        "recovery:\n  stale_task_after_seconds: 42\n"
        "streaming:\n  poll_interval_seconds: 0.1\n",
        encoding="utf-8",
    )
    data = load_config(path)
    assert data["retention"]["max_events_per_task"] == 77
    assert data["retention"]["max_event_age_days"] == 30
    assert data["recovery"]["stale_task_after_seconds"] == 42
    assert data["recovery"]["recover_on_startup"] is True
    assert data["streaming"]["poll_interval_seconds"] == 0.1
    assert data["streaming"]["max_replay_events"] == 1000


def test_v023_config_defaults_backfill_and_preserve_existing_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "ownership:\n  lease_seconds: 17\n"
        "sqlite:\n  busy_timeout_ms: 321\n"
        "streaming:\n  replay_gap_status_code: 410\n",
        encoding="utf-8",
    )
    data = load_config(path)
    assert data["ownership"] == {
        "lease_seconds": 17,
        "heartbeat_interval_seconds": 10,
        "recover_expired_leases_on_startup": True,
        "expired_lease_state": "TASK_STATE_FAILED",
    }
    assert data["sqlite"] == {
        "busy_timeout_ms": 321,
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
        "maintenance_vacuum": False,
    }
    assert data["streaming"]["replay_gap_status_code"] == 410
    assert data["streaming"]["replay_gap_error_code"] == "replay_gap"


def test_v024_config_defaults_backfill_and_preserve_existing_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "cancellation:\n  request_ttl_seconds: 45\n"
        "observability:\n  lease_warning_seconds: 8\n"
        "faults:\n  sqlite_retry_attempts: 7\n",
        encoding="utf-8",
    )
    data = load_config(path)
    assert data["cancellation"] == {
        "request_ttl_seconds": 45,
        "poll_interval_seconds": 0.5,
    }
    assert data["observability"] == {
        "lease_warning_seconds": 8,
        "include_diagnostics_in_stats": True,
    }
    assert data["faults"] == {
        "sqlite_retry_attempts": 7,
        "sqlite_retry_backoff_seconds": 0.05,
    }


def test_v030_config_defaults_backfill_and_preserve_existing_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "parts:\n  max_data_part_bytes: 123\n"
        "artifacts:\n  parse_json_output: false\n",
        encoding="utf-8",
    )
    data = load_config(path)
    assert data["parts"] == {
        "max_data_part_bytes": 123,
        "allow_data_parts": True,
        "allow_file_parts": False,
        "allow_file_id_references": False,
        "allow_remote_url_file_references": False,
        "allow_inline_file_bytes": False,
    }
    assert data["artifacts"] == {
        "parse_json_output": False,
        "max_artifact_data_bytes": 65536,
    }


def test_v032_file_config_defaults_backfill_and_preserve_existing_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "parts:\n  allow_file_parts: false\n"
        "files:\n"
        "  storage_dir: C:/safe/a2a/files\n"
        "  max_file_bytes: 99\n"
        "  allowed_mime_types:\n"
        "    - text/plain\n",
        encoding="utf-8",
    )
    data = load_config(path)
    assert data["parts"]["allow_file_parts"] is False
    assert data["parts"]["allow_file_id_references"] is False
    assert data["parts"]["allow_remote_url_file_references"] is False
    assert data["parts"]["allow_inline_file_bytes"] is False
    assert data["files"]["storage_dir"] == "C:/safe/a2a/files"
    assert data["files"]["max_file_bytes"] == 99
    assert data["files"]["allowed_mime_types"] == ["text/plain"]
    assert data["files"]["max_total_storage_bytes"] == 1073741824
    assert data["files"]["reject_unknown_mime"] is True
    assert data["files"]["auto_fetch_remote_urls"] is False
    assert data["files"]["allow_inline_bytes"] is False
    assert data["files"]["shard_depth"] == 2


def test_inbound_file_reference_gate_defaults_backfill_and_preserve_existing_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "parts:\n"
        "  allow_file_parts: true\n"
        "  allow_file_id_references: true\n"
        "files:\n"
        "  auto_fetch_remote_urls: false\n",
        encoding="utf-8",
    )
    data = load_config(path)
    assert data["parts"]["allow_file_parts"] is True
    assert data["parts"]["allow_file_id_references"] is True
    assert data["parts"]["allow_remote_url_file_references"] is False
    assert data["parts"]["allow_inline_file_bytes"] is False
    assert data["files"]["auto_fetch_remote_urls"] is False
    assert data["files"]["allow_inline_bytes"] is False


def test_invalid_yaml_is_clear_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("server: [oops\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unable to read config"):
        load_config(path)


def test_runtime_and_package_source_versions_match():
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert hermes_a2a_bridge.__version__ == project["project"]["version"] == "0.4.7"
    assert default_config()["agent_card"]["version"] == "0.4.7"
