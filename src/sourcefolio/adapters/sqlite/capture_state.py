from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sourcefolio.application.capture_types import (
    AttemptStatus,
    CaptureAttempt,
    CaptureJob,
    CaptureJobStatus,
)
from sourcefolio.domain.capture import CaptureTask, OperationHash, TaskId
from sourcefolio.domain.common import canonical_json
from sourcefolio.domain.source import CaptureId

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_jobs (
    task_id TEXT PRIMARY KEY,
    operation_hash TEXT NOT NULL,
    task_json TEXT NOT NULL,
    status TEXT NOT NULL,
    capture_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS capture_jobs_status_idx
ON capture_jobs(status);

CREATE TABLE IF NOT EXISTS capture_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    operation_hash TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(task_id, operation_hash, attempt_number),
    FOREIGN KEY(task_id) REFERENCES capture_jobs(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS capture_attempts_task_idx
ON capture_attempts(task_id, operation_hash, attempt_number);
"""


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("control timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLiteCaptureState:
    """Short synchronous transactions on the event-loop thread; no DB work in workers."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteCaptureState:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def register(
        self,
        task: CaptureTask,
        operation_hash: OperationHash,
        now: datetime,
    ) -> bool:
        timestamp = _iso(now)
        task_json = canonical_json(task)
        with self._transaction():
            row = self._connection.execute(
                "SELECT operation_hash, status FROM capture_jobs WHERE task_id = ?",
                (task.task_id,),
            ).fetchone()
            if row is not None:
                if row["operation_hash"] == operation_hash and row["status"] == "succeeded":
                    return False
                self._connection.execute(
                    """
                    UPDATE capture_attempts
                    SET status = 'interrupted', finished_at = ?
                    WHERE task_id = ? AND status IN ('running', 'retrying')
                    """,
                    (timestamp, task.task_id),
                )
                self._connection.execute(
                    """
                    UPDATE capture_jobs
                    SET operation_hash = ?, task_json = ?, status = 'pending',
                        capture_id = NULL, last_error = NULL, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (operation_hash, task_json, timestamp, task.task_id),
                )
                return True

            self._connection.execute(
                """
                INSERT INTO capture_jobs (
                    task_id, operation_hash, task_json, status, capture_id,
                    last_error, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', NULL, NULL, ?, ?)
                """,
                (
                    task.task_id,
                    operation_hash,
                    task_json,
                    timestamp,
                    timestamp,
                ),
            )
            return True

    def claim(self, task_id: TaskId, operation_hash: OperationHash, now: datetime) -> bool:
        with self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE capture_jobs
                SET status = 'running', updated_at = ?
                WHERE task_id = ? AND operation_hash = ?
                  AND status IN ('pending', 'partial', 'failed')
                """,
                (_iso(now), task_id, operation_hash),
            )
            return cursor.rowcount == 1

    def start_attempt(
        self,
        task_id: TaskId,
        operation_hash: OperationHash,
        started_at: datetime,
    ) -> int:
        with self._transaction():
            attempt_number = self._connection.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1
                FROM capture_attempts
                WHERE task_id = ? AND operation_hash = ?
                """,
                (task_id, operation_hash),
            ).fetchone()[0]
            cursor = self._connection.execute(
                """
                INSERT INTO capture_attempts (
                    task_id, operation_hash, attempt_number, status,
                    error, started_at, finished_at
                ) VALUES (?, ?, ?, 'running', NULL, ?, NULL)
                """,
                (task_id, operation_hash, attempt_number, _iso(started_at)),
            )
            attempt_id = cursor.lastrowid
            if attempt_id is None:
                raise RuntimeError("SQLite did not return an attempt id")
            return attempt_id

    def finish_attempt(
        self,
        attempt_id: int,
        status: AttemptStatus,
        finished_at: datetime,
        error: str | None = None,
    ) -> None:
        with self._transaction():
            self._connection.execute(
                """
                UPDATE capture_attempts
                SET status = ?, error = ?, finished_at = ?
                WHERE attempt_id = ?
                """,
                (status.value, error, _iso(finished_at), attempt_id),
            )

    def finish_job(
        self,
        task_id: TaskId,
        operation_hash: OperationHash,
        status: CaptureJobStatus,
        finished_at: datetime,
        capture_id: CaptureId | None = None,
        error: str | None = None,
    ) -> None:
        if status in {CaptureJobStatus.PENDING, CaptureJobStatus.RUNNING}:
            raise ValueError("finish_job requires a terminal or retryable outcome")
        with self._transaction():
            self._connection.execute(
                """
                UPDATE capture_jobs
                SET status = ?, capture_id = ?, last_error = ?, updated_at = ?
                WHERE task_id = ? AND operation_hash = ?
                """,
                (
                    status.value,
                    capture_id,
                    error,
                    _iso(finished_at),
                    task_id,
                    operation_hash,
                ),
            )

    def get(self, task_id: TaskId) -> CaptureJob | None:
        row = self._connection.execute(
            "SELECT * FROM capture_jobs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return CaptureJob(
            task_id=row["task_id"],
            operation_hash=row["operation_hash"],
            status=CaptureJobStatus(row["status"]),
            capture_id=row["capture_id"],
            last_error=row["last_error"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    def attempts(self, task_id: TaskId) -> tuple[CaptureAttempt, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM capture_attempts
            WHERE task_id = ? ORDER BY attempt_id
            """,
            (task_id,),
        ).fetchall()
        return tuple(
            CaptureAttempt(
                attempt_id=row["attempt_id"],
                task_id=row["task_id"],
                operation_hash=row["operation_hash"],
                attempt_number=row["attempt_number"],
                status=AttemptStatus(row["status"]),
                error=row["error"],
                started_at=_datetime(row["started_at"]),
                finished_at=(
                    _datetime(row["finished_at"]) if row["finished_at"] is not None else None
                ),
            )
            for row in rows
        )

    def iter_jobs(self) -> Iterator[tuple[CaptureJob, CaptureTask]]:
        rows = self._connection.execute("SELECT * FROM capture_jobs ORDER BY created_at, task_id")
        for row in rows:
            task = CaptureTask.model_validate_json(row["task_json"])
            if task.task_id != row["task_id"]:
                raise ValueError("capture job task payload does not match its task_id")
            job = CaptureJob(
                task_id=row["task_id"],
                operation_hash=row["operation_hash"],
                status=CaptureJobStatus(row["status"]),
                capture_id=row["capture_id"],
                last_error=row["last_error"],
                created_at=_datetime(row["created_at"]),
                updated_at=_datetime(row["updated_at"]),
            )
            yield job, task

    def iter_attempts(self) -> Iterator[CaptureAttempt]:
        rows = self._connection.execute("SELECT * FROM capture_attempts ORDER BY attempt_id")
        for row in rows:
            yield CaptureAttempt(
                attempt_id=row["attempt_id"],
                task_id=row["task_id"],
                operation_hash=row["operation_hash"],
                attempt_number=row["attempt_number"],
                status=AttemptStatus(row["status"]),
                error=row["error"],
                started_at=_datetime(row["started_at"]),
                finished_at=(
                    _datetime(row["finished_at"]) if row["finished_at"] is not None else None
                ),
            )
