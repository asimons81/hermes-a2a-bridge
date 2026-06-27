"""SQLite persistence for tasks and the local remote-agent registry."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from .errors import DatabaseBusyError
from .models import Message, Task, TaskState, TaskStatus, utc_now

T = TypeVar("T")


@dataclass(frozen=True)
class StoredEvent:
    id: int
    task_id: str
    created_at: str
    event: dict[str, Any]

    def envelope(self) -> dict[str, Any]:
        return {"id": self.id, "event": "message", "data": self.event}


class Store:
    def __init__(
        self,
        path: Path | str,
        sqlite_config: dict[str, Any] | None = None,
        fault_config: dict[str, Any] | None = None,
    ):
        self.path = str(path)
        self._sqlite_config: dict[str, Any] = {}
        self._sqlite_warnings: list[str] = []
        self._sqlite_retry_count = 0
        self._sqlite_retry_exhausted_count = 0
        self._sleep = time.sleep
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.configure_sqlite(sqlite_config or {}, fault_config or {})
        self._create_tables()

    def _connect(self) -> sqlite3.Connection:
        busy_timeout_ms = max(0, int(self._sqlite_config.get("busy_timeout_ms", 5000)))
        conn = sqlite3.connect(self.path, timeout=busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        self._apply_pragmas(conn)
        return conn

    def configure_sqlite(
        self, sqlite_config: dict[str, Any], fault_config: dict[str, Any] | None = None,
    ) -> None:
        try:
            busy_timeout_ms = max(0, int(sqlite_config.get("busy_timeout_ms", 5000)))
        except (TypeError, ValueError) as exc:
            busy_timeout_ms = 5000
            self._warn_sqlite(f"Invalid SQLite busy_timeout_ms; using 5000: {exc}")
        self._sqlite_config = {
            "busy_timeout_ms": busy_timeout_ms,
            "journal_mode": str(sqlite_config.get("journal_mode", "WAL")),
            "synchronous": str(sqlite_config.get("synchronous", "NORMAL")),
            "maintenance_vacuum": bool(sqlite_config.get("maintenance_vacuum", False)),
        }
        faults = fault_config or {}
        try:
            retry_attempts = max(0, int(faults.get("sqlite_retry_attempts", 3)))
            retry_backoff = max(0.0, float(faults.get("sqlite_retry_backoff_seconds", 0.05)))
        except (TypeError, ValueError) as exc:
            retry_attempts, retry_backoff = 3, 0.05
            self._warn_sqlite(f"Invalid SQLite retry configuration; using defaults: {exc}")
        self._fault_config = {
            "sqlite_retry_attempts": retry_attempts,
            "sqlite_retry_backoff_seconds": retry_backoff,
        }

    @staticmethod
    def _is_transient_sqlite_error(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    def run_with_sqlite_retry(self, operation: Callable[[], T], *, label: str = "write") -> T:
        retries = int(self._fault_config.get("sqlite_retry_attempts", 3))
        backoff = float(self._fault_config.get("sqlite_retry_backoff_seconds", 0.05))
        for attempt in range(retries + 1):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if not self._is_transient_sqlite_error(exc):
                    raise
                if attempt >= retries:
                    self._sqlite_retry_exhausted_count += 1
                    raise DatabaseBusyError(
                        f"SQLite {label} failed after {retries + 1} attempts because the database remained busy or locked."
                    ) from exc
                self._sqlite_retry_count += 1
                self._sleep(backoff * (attempt + 1))
        raise AssertionError("unreachable")

    def _warn_sqlite(self, message: str) -> None:
        if message not in self._sqlite_warnings:
            self._sqlite_warnings.append(message)

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute(f"PRAGMA busy_timeout={max(0, int(self._sqlite_config['busy_timeout_ms']))}")
        except (KeyError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
            self._warn_sqlite(f"Unable to apply SQLite busy_timeout: {exc}")
        journal_mode = str(self._sqlite_config.get("journal_mode", "WAL")).upper()
        if journal_mode not in {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}:
            self._warn_sqlite(f"Unsupported SQLite journal_mode ignored: {journal_mode}")
        else:
            try:
                actual = str(conn.execute(f"PRAGMA journal_mode={journal_mode}").fetchone()[0]).upper()
                if actual != journal_mode:
                    self._warn_sqlite(
                        f"SQLite journal_mode requested {journal_mode} but database uses {actual}"
                    )
            except sqlite3.DatabaseError as exc:
                self._warn_sqlite(f"Unable to apply SQLite journal_mode {journal_mode}: {exc}")
        synchronous = str(self._sqlite_config.get("synchronous", "NORMAL")).upper()
        if synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA", "0", "1", "2", "3"}:
            self._warn_sqlite(f"Unsupported SQLite synchronous setting ignored: {synchronous}")
        else:
            try:
                conn.execute(f"PRAGMA synchronous={synchronous}")
            except sqlite3.DatabaseError as exc:
                self._warn_sqlite(f"Unable to apply SQLite synchronous {synchronous}: {exc}")

    def _create_tables(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    context_id TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT,
                    error TEXT,
                    metadata_json TEXT
                );
                CREATE TABLE IF NOT EXISTS registry (
                    name TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    token TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task_id_id
                    ON task_events(task_id, id);
                CREATE TABLE IF NOT EXISTS task_leases (
                    task_id TEXT PRIMARY KEY,
                    owner_instance_id TEXT NOT NULL,
                    owner_pid INTEGER NOT NULL,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_leases_expires_at
                    ON task_leases(lease_expires_at);
                CREATE TABLE IF NOT EXISTS cancellation_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    requester_instance_id TEXT NOT NULL,
                    owner_instance_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    reason TEXT,
                    acknowledged_at TEXT,
                    completed_at TEXT,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cancellation_requests_owner_status
                    ON cancellation_requests(owner_instance_id, status, task_id);
                CREATE INDEX IF NOT EXISTS idx_cancellation_requests_expires
                    ON cancellation_requests(expires_at, status);
                CREATE TABLE IF NOT EXISTS file_attachments (
                    id TEXT PRIMARY KEY,
                    task_id TEXT,
                    artifact_id TEXT,
                    filename TEXT,
                    safe_filename TEXT,
                    mime_type TEXT,
                    declared_mime_type TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    storage_path TEXT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_file_attachments_task_id
                    ON file_attachments(task_id);
                CREATE INDEX IF NOT EXISTS idx_file_attachments_artifact_id
                    ON file_attachments(artifact_id);
                CREATE INDEX IF NOT EXISTS idx_file_attachments_created_at
                    ON file_attachments(created_at);
            """)
            columns = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(file_attachments)").fetchall()
            }
            expected_file_columns = (
                "id", "task_id", "artifact_id", "filename", "safe_filename", "mime_type",
                "declared_mime_type", "size_bytes", "sha256", "storage_path", "created_at",
                "source", "source_url", "metadata_json",
            )
            nullable_regression = any(
                columns[name]["notnull"] for name in ("size_bytes", "sha256", "storage_path") if name in columns
            )
            missing_file_columns = [name for name in expected_file_columns if name not in columns]
            if nullable_regression or missing_file_columns:
                copy_expressions = []
                for name in expected_file_columns:
                    if name in columns:
                        copy_expressions.append(name)
                    elif name == "source_url":
                        copy_expressions.append("NULL AS source_url")
                    elif name == "metadata_json":
                        copy_expressions.append("'{}' AS metadata_json")
                    elif name == "source":
                        copy_expressions.append("'local' AS source")
                    else:
                        copy_expressions.append(f"NULL AS {name}")
                conn.executescript("""
                    ALTER TABLE file_attachments RENAME TO file_attachments_old;
                    CREATE TABLE file_attachments (
                        id TEXT PRIMARY KEY,
                        task_id TEXT,
                        artifact_id TEXT,
                        filename TEXT,
                        safe_filename TEXT,
                        mime_type TEXT,
                        declared_mime_type TEXT,
                        size_bytes INTEGER,
                        sha256 TEXT,
                        storage_path TEXT,
                        created_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        source_url TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );
                """)
                conn.execute(
                    f"""INSERT INTO file_attachments ({", ".join(expected_file_columns)})
                        SELECT {", ".join(copy_expressions)}
                        FROM file_attachments_old"""
                )
                conn.executescript("""
                    DROP TABLE file_attachments_old;
                    CREATE INDEX IF NOT EXISTS idx_file_attachments_task_id
                        ON file_attachments(task_id);
                    CREATE INDEX IF NOT EXISTS idx_file_attachments_artifact_id
                        ON file_attachments(artifact_id);
                    CREATE INDEX IF NOT EXISTS idx_file_attachments_created_at
                        ON file_attachments(created_at);
                """)

    def add_file_attachment(
        self,
        *,
        file_id: str,
        task_id: str | None = None,
        artifact_id: str | None = None,
        filename: str | None = None,
        safe_filename: str | None = None,
        mime_type: str | None = None,
        declared_mime_type: str | None = None,
        size_bytes: int | None,
        sha256: str | None,
        storage_path: str | None,
        source: str = "local",
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        created = created_at or utc_now()

        def write() -> dict[str, Any]:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO file_attachments (
                           id, task_id, artifact_id, filename, safe_filename, mime_type,
                           declared_mime_type, size_bytes, sha256, storage_path, created_at,
                           source, source_url, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        file_id, task_id, artifact_id, filename, safe_filename, mime_type,
                        declared_mime_type, int(size_bytes) if size_bytes is not None else None,
                        sha256, storage_path, created,
                        source, source_url, metadata_json,
                    ),
                )
            item = self.get_file_attachment(file_id)
            if item is None:
                raise sqlite3.DatabaseError("Inserted file attachment could not be read back")
            return item

        return self.run_with_sqlite_retry(write, label="file attachment metadata write")

    def get_file_attachment(self, file_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM file_attachments WHERE id=?", (file_id,)).fetchone()
        return self._row_to_file_attachment(row) if row else None

    def list_file_attachments(
        self,
        task_id: str | None = None,
        artifact_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM file_attachments"
        clauses: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            clauses.append("task_id=?")
            params.append(task_id)
        if artifact_id is not None:
            clauses.append("artifact_id=?")
            params.append(artifact_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC, id ASC"
        if limit is not None:
            if limit < 1:
                return []
            query += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_file_attachment(row) for row in rows]

    def delete_file_attachment(self, file_id: str) -> bool:
        def write() -> bool:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM file_attachments WHERE id=?", (file_id,))
            return cursor.rowcount > 0
        return self.run_with_sqlite_retry(write, label="file attachment metadata delete")

    def count_file_attachments(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM file_attachments").fetchone()[0])

    def file_storage_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS total FROM file_attachments"
            ).fetchone()
        return {
            "file_attachment_count": int(row["count"]),
            "file_attachment_bytes": int(row["total"]),
        }

    def add_task_event(self, task_id: str, event: dict[str, Any]) -> int:
        def write() -> int:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO task_events (task_id, created_at, event_json) VALUES (?, ?, ?)",
                    (task_id, utc_now(), json.dumps(event, ensure_ascii=False, separators=(",", ":"))),
                )
            return int(cursor.lastrowid)
        return self.run_with_sqlite_retry(write, label="task event write")

    def list_task_events(
        self,
        task_id: str,
        after_event_id: int | None = None,
        limit: int | None = None,
    ) -> list[StoredEvent]:
        query = "SELECT id, task_id, created_at, event_json FROM task_events WHERE task_id=?"
        params: list[Any] = [task_id]
        if after_event_id is not None:
            query += " AND id>?"
            params.append(after_event_id)
        query += " ORDER BY id ASC"
        if limit is not None:
            if limit < 1:
                return []
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_event(row) for row in rows]

    def get_task_event(self, task_id: str, event_id: int) -> StoredEvent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, task_id, created_at, event_json FROM task_events WHERE task_id=? AND id=?",
                (task_id, event_id),
            ).fetchone()
        return self._row_to_event(row) if row else None

    def count_task_events(self, task_id: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM task_events"
        params: tuple[Any, ...] = ()
        if task_id is not None:
            query += " WHERE task_id=?"
            params = (task_id,)
        with self._connect() as conn:
            return int(conn.execute(query, params).fetchone()[0])

    def get_event_bounds(self, task_id: str | None = None) -> dict[str, Any]:
        where = " WHERE task_id=?" if task_id is not None else ""
        params = (task_id,) if task_id is not None else ()
        with self._connect() as conn:
            oldest = conn.execute(
                f"SELECT id, created_at FROM task_events{where} ORDER BY id ASC LIMIT 1", params,
            ).fetchone()
            newest = conn.execute(
                f"SELECT id, created_at FROM task_events{where} ORDER BY id DESC LIMIT 1", params,
            ).fetchone()
        return {
            "oldest_event_id": int(oldest["id"]) if oldest else None,
            "newest_event_id": int(newest["id"]) if newest else None,
            "oldest_event_created_at": oldest["created_at"] if oldest else None,
            "newest_event_created_at": newest["created_at"] if newest else None,
        }

    def prune_task_events(
        self,
        max_events_per_task: int | None = None,
        older_than_days: int | None = None,
    ) -> dict[str, int]:
        if max_events_per_task is not None and max_events_per_task < 0:
            raise ValueError("max_events_per_task must be non-negative")
        if older_than_days is not None and older_than_days < 0:
            raise ValueError("older_than_days must be non-negative")
        with self._connect() as conn:
            before_rows = conn.execute(
                "SELECT task_id, COUNT(*) AS count FROM task_events GROUP BY task_id"
            ).fetchall()
            before = {row["task_id"]: int(row["count"]) for row in before_rows}
            if older_than_days is not None:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
                conn.execute("DELETE FROM task_events WHERE created_at < ?", (cutoff,))
            if max_events_per_task is not None:
                conn.execute(
                    """DELETE FROM task_events WHERE id IN (
                           SELECT id FROM (
                               SELECT id, ROW_NUMBER() OVER (
                                   PARTITION BY task_id ORDER BY id DESC
                               ) AS row_number
                               FROM task_events
                           ) WHERE row_number > ?
                       )""",
                    (max_events_per_task,),
                )
            after_rows = conn.execute(
                "SELECT task_id, COUNT(*) AS count FROM task_events GROUP BY task_id"
            ).fetchall()
            after = {row["task_id"]: int(row["count"]) for row in after_rows}
        affected = sum(1 for task_id, count in before.items() if after.get(task_id, 0) != count)
        remaining = sum(after.values())
        return {
            "deleted_count": sum(before.values()) - remaining,
            "remaining_count": remaining,
            "affected_task_count": affected,
        }

    @staticmethod
    def _lease_time(value: datetime | str | None = None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc)

    def acquire_task_lease(
        self,
        task_id: str,
        owner_instance_id: str,
        owner_pid: int,
        lease_seconds: int | float,
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self._lease_time()
        now_text = now.isoformat()
        expires = (now + timedelta(seconds=float(lease_seconds))).isoformat()
        def write() -> bool:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                task = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not task or task["state"] not in {
                    TaskState.SUBMITTED.value, TaskState.WORKING.value,
                }:
                    return False
                lease = conn.execute("SELECT * FROM task_leases WHERE task_id=?", (task_id,)).fetchone()
                if lease and lease["owner_instance_id"] != owner_instance_id and lease["lease_expires_at"] > now_text:
                    return False
                acquired_at = (
                    lease["acquired_at"]
                    if lease and lease["owner_instance_id"] == owner_instance_id
                    else now_text
                )
                conn.execute(
                    """INSERT INTO task_leases
                           (task_id, owner_instance_id, owner_pid, acquired_at, heartbeat_at, lease_expires_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(task_id) DO UPDATE SET
                           owner_instance_id=excluded.owner_instance_id,
                           owner_pid=excluded.owner_pid,
                           acquired_at=excluded.acquired_at,
                           heartbeat_at=excluded.heartbeat_at,
                           lease_expires_at=excluded.lease_expires_at""",
                    (task_id, owner_instance_id, int(owner_pid), acquired_at, now_text, expires),
                )
            return True
        return self.run_with_sqlite_retry(write, label="lease acquisition")

    def heartbeat_task_lease(
        self, task_id: str, owner_instance_id: str, lease_seconds: int | float,
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self._lease_time()
        def write() -> bool:
            with self._connect() as conn:
                cursor = conn.execute(
                    """UPDATE task_leases SET heartbeat_at=?, lease_expires_at=?
                       WHERE task_id=? AND owner_instance_id=? AND lease_expires_at>?""",
                    (
                        now.isoformat(),
                        (now + timedelta(seconds=float(lease_seconds))).isoformat(),
                        task_id,
                        owner_instance_id,
                        now.isoformat(),
                    ),
                )
            return cursor.rowcount > 0
        return self.run_with_sqlite_retry(write, label="lease heartbeat")

    def release_task_lease(self, task_id: str, owner_instance_id: str) -> bool:
        def write() -> bool:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM task_leases WHERE task_id=? AND owner_instance_id=?",
                    (task_id, owner_instance_id),
                )
            return cursor.rowcount > 0
        return self.run_with_sqlite_retry(write, label="lease release")

    def get_task_lease(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM task_leases WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_task_leases(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM task_leases ORDER BY lease_expires_at, task_id").fetchall()
        return [dict(row) for row in rows]

    def lease_diagnostics(
        self, warning_seconds: int | float = 20, now: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        current = self._lease_time(now)
        warning = max(0.0, float(warning_seconds))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT task_leases.*, tasks.state
                   FROM task_leases LEFT JOIN tasks ON tasks.id=task_leases.task_id
                   ORDER BY task_leases.lease_expires_at, task_leases.task_id"""
            ).fetchall()
        diagnostics = []
        for row in rows:
            item = dict(row)
            acquired = self._lease_time(item["acquired_at"])
            heartbeat = self._lease_time(item["heartbeat_at"])
            expires = self._lease_time(item["lease_expires_at"])
            lease_age = max(0.0, (current - acquired).total_seconds())
            heartbeat_age = max(0.0, (current - heartbeat).total_seconds())
            until_expiry = (expires - current).total_seconds()
            expired = until_expiry <= 0
            item.update({
                "lease_age_seconds": lease_age,
                "seconds_until_expiry": until_expiry,
                "heartbeat_age_seconds": heartbeat_age,
                "lease_expiring_soon": not expired and until_expiry <= warning,
                "heartbeat_stale": heartbeat_age >= warning,
                "expired": expired,
            })
            diagnostics.append(item)
        return diagnostics

    def create_cancellation_request(
        self,
        task_id: str,
        requester_instance_id: str,
        owner_instance_id: str | None,
        ttl_seconds: int | float,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("cancellation request ttl_seconds must be positive")
        now = self._lease_time()
        now_text = now.isoformat()
        expires = (now + timedelta(seconds=float(ttl_seconds))).isoformat()

        def write() -> dict[str, Any]:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    """SELECT * FROM cancellation_requests
                       WHERE task_id=? AND owner_instance_id IS ? AND status='pending' AND expires_at>?
                       ORDER BY id DESC LIMIT 1""",
                    (task_id, owner_instance_id, now_text),
                ).fetchone()
                if existing:
                    return dict(existing)
                cursor = conn.execute(
                    """INSERT INTO cancellation_requests
                       (task_id, requester_instance_id, owner_instance_id, created_at, expires_at,
                        reason, acknowledged_at, completed_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pending')""",
                    (task_id, requester_instance_id, owner_instance_id, now_text, expires, reason),
                )
                row = conn.execute(
                    "SELECT * FROM cancellation_requests WHERE id=?", (cursor.lastrowid,),
                ).fetchone()
            return dict(row)

        return self.run_with_sqlite_retry(write, label="cancellation request creation")

    def list_cancellation_requests(
        self,
        task_id: str | None = None,
        status: str | None = None,
        owner_instance_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            clauses.append("task_id=?")
            params.append(task_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if owner_instance_id is not None:
            clauses.append("owner_instance_id=?")
            params.append(owner_instance_id)
        query = "SELECT * FROM cancellation_requests"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_cancellation_request(self, request_id: int, owner_instance_id: str) -> bool:
        now = self._lease_time().isoformat()

        def write() -> bool:
            with self._connect() as conn:
                cursor = conn.execute(
                    """UPDATE cancellation_requests
                       SET status='acknowledged', acknowledged_at=?
                       WHERE id=? AND owner_instance_id=? AND status='pending' AND expires_at>?""",
                    (now, int(request_id), owner_instance_id, now),
                )
            return cursor.rowcount > 0

        return self.run_with_sqlite_retry(write, label="cancellation acknowledgement")

    def complete_cancellation_request(self, request_id: int, owner_instance_id: str) -> bool:
        now = self._lease_time().isoformat()

        def write() -> bool:
            with self._connect() as conn:
                cursor = conn.execute(
                    """UPDATE cancellation_requests
                       SET status='completed', completed_at=?
                       WHERE id=? AND owner_instance_id=? AND status='acknowledged'""",
                    (now, int(request_id), owner_instance_id),
                )
            return cursor.rowcount > 0

        return self.run_with_sqlite_retry(write, label="cancellation completion")

    def expire_cancellation_requests(self, now: datetime | str | None = None) -> dict[str, Any]:
        now_text = self._lease_time(now).isoformat()

        def write() -> dict[str, Any]:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT id FROM cancellation_requests
                       WHERE status IN ('pending', 'acknowledged') AND expires_at<=? ORDER BY id""",
                    (now_text,),
                ).fetchall()
                ids = [int(row["id"]) for row in rows]
                if ids:
                    conn.execute(
                        f"UPDATE cancellation_requests SET status='expired' WHERE id IN ({','.join('?' for _ in ids)})",
                        tuple(ids),
                    )
            return {"expired_count": len(ids), "expired_request_ids": ids}

        return self.run_with_sqlite_retry(write, label="cancellation expiry")

    def get_pending_cancellation_for_owner(
        self, task_id: str, owner_instance_id: str,
    ) -> dict[str, Any] | None:
        now = self._lease_time().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM cancellation_requests
                   WHERE task_id=? AND owner_instance_id=? AND status='pending' AND expires_at>?
                   ORDER BY id ASC LIMIT 1""",
                (task_id, owner_instance_id, now),
            ).fetchone()
        return dict(row) if row else None

    def list_expired_leases(self, now: datetime | str | None = None) -> list[dict[str, Any]]:
        now_text = self._lease_time(now).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_leases WHERE lease_expires_at<=? ORDER BY lease_expires_at, task_id",
                (now_text,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_expired_leases(
        self,
        target_state: TaskState = TaskState.FAILED,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        if target_state not in {TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}:
            raise ValueError("expired lease target must be FAILED, CANCELED, or REJECTED")
        now_text = self._lease_time(now).isoformat()
        message = "Task marked failed because its executor lease expired before completion."
        if target_state != TaskState.FAILED:
            action = target_state.value.removeprefix("TASK_STATE_").lower()
            message = f"Task marked {action} because its executor lease expired before completion."
        expired = self.list_expired_leases(now_text)
        recovered_ids: list[str] = []
        for lease in expired:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM tasks WHERE id=?", (lease["task_id"],)).fetchone()
                current_lease = conn.execute(
                    "SELECT * FROM task_leases WHERE task_id=?", (lease["task_id"],),
                ).fetchone()
                if (
                    not row
                    or row["state"] not in {TaskState.SUBMITTED.value, TaskState.WORKING.value, TaskState.INPUT_REQUIRED.value}
                    or not current_lease
                    or current_lease["lease_expires_at"] > now_text
                ):
                    if current_lease and current_lease["lease_expires_at"] <= now_text and (
                        not row
                        or row["state"] not in {
                            TaskState.SUBMITTED.value, TaskState.WORKING.value, TaskState.INPUT_REQUIRED.value,
                        }
                    ):
                        conn.execute("DELETE FROM task_leases WHERE task_id=?", (lease["task_id"],))
                    continue
                metadata = json.loads(row["metadata_json"] or "{}")
                metadata["recovery"] = {"status": "recovered", "reason": message, "expiredLease": True}
                cursor = conn.execute(
                    """UPDATE tasks SET state=?, updated_at=?, error=?, metadata_json=?
                       WHERE id=? AND state IN (?, ?, ?)""",
                    (
                        target_state.value, utc_now(), message, json.dumps(metadata), lease["task_id"],
                        TaskState.SUBMITTED.value, TaskState.WORKING.value, TaskState.INPUT_REQUIRED.value,
                    ),
                )
                if not cursor.rowcount:
                    continue
                conn.execute(
                    "DELETE FROM task_leases WHERE task_id=? AND lease_expires_at<=?",
                    (lease["task_id"], now_text),
                )
                conn.execute(
                    """UPDATE cancellation_requests SET status='expired'
                       WHERE task_id=? AND status IN ('pending', 'acknowledged')""",
                    (lease["task_id"],),
                )
            recovered = self.get_task(lease["task_id"])
            event = {
                "statusUpdate": {
                    "taskId": recovered.id,
                    "contextId": recovered.context_id or "",
                    "status": recovered.status.model_dump(by_alias=True, exclude_none=True, mode="json"),
                    "metadata": {"final": True, "recovery": True, "expiredLease": True},
                }
            }
            self.add_task_event(recovered.id, event)
            recovered_ids.append(recovered.id)
        return {
            "recovered_count": len(recovered_ids),
            "skipped_count": max(0, len(expired) - len(recovered_ids)),
            "recovered_task_ids": recovered_ids,
        }

    def list_stale_tasks(self, states: set[TaskState], older_than_seconds: int) -> list[Task]:
        if older_than_seconds < 0:
            raise ValueError("older_than_seconds must be non-negative")
        if not states:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        state_values = tuple(sorted(state.value for state in states))
        placeholders = ",".join("?" for _ in state_values)
        now = self._lease_time().isoformat()
        params = state_values + (cutoff, now)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM tasks
                    WHERE state IN ({placeholders}) AND updated_at < ?
                      AND NOT EXISTS (
                          SELECT 1 FROM task_leases
                          WHERE task_leases.task_id=tasks.id AND task_leases.lease_expires_at>?
                      )
                    ORDER BY updated_at ASC""",
                params,
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def recover_stale_tasks(
        self,
        states: set[TaskState],
        older_than_seconds: int,
        target_state: TaskState = TaskState.FAILED,
    ) -> dict[str, Any]:
        action = {
            TaskState.FAILED: "failed",
            TaskState.CANCELED: "canceled",
            TaskState.REJECTED: "rejected",
        }.get(target_state, target_state.value)
        message = (
            f"Task marked {action} during startup recovery because the previous server process "
            "exited before completion."
        )
        stale = self.list_stale_tasks(states, older_than_seconds)
        state_values = tuple(sorted(state.value for state in states))
        with self._connect() as conn:
            total_nonterminal = int(conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE state IN ({','.join('?' for _ in state_values)})",
                state_values,
            ).fetchone()[0]) if states else 0
        recovered_ids: list[str] = []
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        for task in stale:
            now_text = self._lease_time().isoformat()
            with self._connect() as conn:
                cursor = conn.execute(
                    f"""UPDATE tasks SET state=?, updated_at=?, error=?, metadata_json=?
                        WHERE id=? AND state IN ({','.join('?' for _ in state_values)}) AND updated_at < ?
                          AND NOT EXISTS (
                              SELECT 1 FROM task_leases
                              WHERE task_leases.task_id=tasks.id AND task_leases.lease_expires_at>?
                          )""",
                    (
                        target_state.value,
                        utc_now(),
                        message,
                        json.dumps({"recovery": {"status": "recovered", "reason": message}}),
                        task.id,
                        *state_values,
                        cutoff,
                        now_text,
                    ),
                )
            if cursor.rowcount:
                with self._connect() as conn:
                    conn.execute(
                        "DELETE FROM task_leases WHERE task_id=? AND lease_expires_at<=?",
                        (task.id, now_text),
                    )
                    conn.execute(
                        """UPDATE cancellation_requests SET status='expired'
                           WHERE task_id=? AND status IN ('pending', 'acknowledged')""",
                        (task.id,),
                    )
                recovered = self.get_task(task.id)
                event = {
                    "statusUpdate": {
                        "taskId": recovered.id,
                        "contextId": recovered.context_id or "",
                        "status": recovered.status.model_dump(by_alias=True, exclude_none=True, mode="json"),
                        "metadata": {"final": True, "recovery": True},
                    }
                }
                self.add_task_event(task.id, event)
                recovered_ids.append(task.id)
        return {
            "recovered_count": len(recovered_ids),
            "skipped_count": max(0, total_nonterminal - len(recovered_ids)),
            "recovered_task_ids": recovered_ids,
        }

    def maintenance_stats(self, lease_warning_seconds: int | float = 20) -> dict[str, Any]:
        with self._connect() as conn:
            task_count = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            registry_count = int(conn.execute("SELECT COUNT(*) FROM registry").fetchone()[0])
            lease_count = int(conn.execute("SELECT COUNT(*) FROM task_leases").fetchone()[0])
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
            synchronous = int(conn.execute("PRAGMA synchronous").fetchone()[0])
            pending_cancellation_count = int(conn.execute(
                "SELECT COUNT(*) FROM cancellation_requests WHERE status='pending'"
            ).fetchone()[0])
            expired_cancellation_count = int(conn.execute(
                "SELECT COUNT(*) FROM cancellation_requests WHERE status='expired'"
            ).fetchone()[0])
            file_attachment_count = int(conn.execute(
                "SELECT COUNT(*) FROM file_attachments"
            ).fetchone()[0])
            file_attachment_bytes = int(conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM file_attachments"
            ).fetchone()[0])
        leases = self.lease_diagnostics(lease_warning_seconds)
        expired_lease_count = sum(1 for lease in leases if lease["expired"])
        database_size = Path(self.path).stat().st_size if Path(self.path).exists() else 0
        return {
            "database_path": self.path,
            "database_size_bytes": database_size,
            "journal_mode": journal_mode,
            "busy_timeout_ms": busy_timeout_ms,
            "synchronous": synchronous,
            "task_count": task_count,
            "event_count": self.count_task_events(),
            "registry_count": registry_count,
            "lease_count": lease_count,
            "active_lease_count": lease_count - expired_lease_count,
            "expired_lease_count": expired_lease_count,
            "stale_heartbeat_count": sum(1 for lease in leases if lease["heartbeat_stale"]),
            "pending_cancellation_count": pending_cancellation_count,
            "expired_cancellation_count": expired_cancellation_count,
            "file_attachment_count": file_attachment_count,
            "file_attachment_bytes": file_attachment_bytes,
            "sqlite_warnings": list(self._sqlite_warnings),
            "sqlite_warning_count": len(self._sqlite_warnings),
            "sqlite_retry_count": self._sqlite_retry_count,
            "sqlite_retry_exhausted_count": self._sqlite_retry_exhausted_count,
            **self.get_event_bounds(),
        }

    def vacuum(self) -> None:
        with self._connect() as conn:
            conn.execute("VACUUM")

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            id=int(row["id"]),
            task_id=row["task_id"],
            created_at=row["created_at"],
            event=json.loads(row["event_json"]),
        )

    @staticmethod
    def _row_to_file_attachment(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        return item

    def insert_task(self, task: Task, request: dict[str, Any]) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.context_id, task.status.state.value, now, now,
                 json.dumps(request), None, None, json.dumps(task.metadata)),
            )

    def update_task(
        self,
        task_id: str,
        state: TaskState,
        response: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        only_if_states: set[TaskState] | None = None,
    ) -> bool:
        def write() -> bool:
            with self._connect() as conn:
                query = "UPDATE tasks SET state=?, updated_at=?, response_json=?, error=?, metadata_json=? WHERE id=?"
                params: list[Any] = [
                    state.value,
                    utc_now(),
                    json.dumps(response) if response is not None else None,
                    error,
                    json.dumps(metadata) if metadata is not None else json.dumps({}),
                    task_id,
                ]
                if only_if_states:
                    placeholders = ",".join("?" for _ in only_if_states)
                    query += f" AND state IN ({placeholders})"
                    params.extend(item.value for item in only_if_states)
                cursor = conn.execute(query, tuple(params))
            return cursor.rowcount > 0
        return self.run_with_sqlite_retry(write, label="task state update")

    def append_task_artifact(self, task_id: str, artifact: dict[str, Any]) -> Task | None:
        def write() -> bool:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT response_json FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row is None:
                    return False
                response = json.loads(row["response_json"]) if row["response_json"] else {}
                if not isinstance(response, dict):
                    response = {}
                artifacts = response.get("artifacts")
                if not isinstance(artifacts, list):
                    artifacts = []
                artifacts.append(artifact)
                response["artifacts"] = artifacts
                conn.execute(
                    "UPDATE tasks SET updated_at=?, response_json=? WHERE id=?",
                    (utc_now(), json.dumps(response, ensure_ascii=False, separators=(",", ":")), task_id),
                )
                return True

        updated = self.run_with_sqlite_retry(write, label="task artifact append")
        return self.get_task(task_id) if updated else None

    def get_task(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, status: str | None = None) -> list[Task]:
        query, params = "SELECT * FROM tasks", ()
        if status:
            query, params = query + " WHERE state=?", (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def cancel_task(self, task_id: str) -> Task | None:
        task = self.get_task(task_id)
        if task and task.status.state in {TaskState.SUBMITTED, TaskState.WORKING}:
            self.update_task(
                task_id,
                TaskState.CANCELED,
                error="Task canceled",
                metadata={**task.metadata, "canceled": True},
                only_if_states={TaskState.SUBMITTED, TaskState.WORKING},
            )
        return self.get_task(task_id)

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        request = json.loads(row["request_json"])
        history = []
        if isinstance(request, dict) and request.get("message"):
            history.append(request["message"])
        response = json.loads(row["response_json"]) if row["response_json"] else None
        status_message = response.get("message") if isinstance(response, dict) else None
        if status_message:
            history.append(status_message)
        artifacts = response.get("artifacts", []) if isinstance(response, dict) else []
        if not status_message and row["error"]:
            status_message = Message(role="agent", parts=[{"text": row["error"]}]).model_dump(
                by_alias=True, exclude_none=True, mode="json"
            )
        return Task(
            id=row["id"], contextId=row["context_id"],
            status=TaskStatus(state=TaskState(row["state"]), timestamp=row["updated_at"], message=status_message),
            history=history,
            artifacts=artifacts,
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def registry_add(self, name: str, url: str, token: str | None = None) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO registry (name,url,token,created_at,updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     url=excluded.url,
                     token=COALESCE(excluded.token, registry.token),
                     updated_at=excluded.updated_at""",
                (name, url, token, now, now),
            )

    def registry_get(self, name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT name,url,token FROM registry WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def registry_list(self, *, include_tokens: bool = False) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name,url,token,created_at,updated_at FROM registry ORDER BY name").fetchall()
        result = []
        for row in rows:
            item = {
                "name": row["name"],
                "url": row["url"],
                "hasToken": bool(row["token"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            if include_tokens:
                item["token"] = row["token"]
            result.append(item)
        return result

    def registry_remove(self, name: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM registry WHERE name=?", (name,))
        return cursor.rowcount > 0
