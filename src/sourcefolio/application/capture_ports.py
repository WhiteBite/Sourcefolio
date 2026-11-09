from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from sourcefolio.application.capture_types import (
    AdapterCapture,
    AttemptStatus,
    CaptureAttempt,
    CaptureJob,
    CaptureJobStatus,
)
from sourcefolio.domain.capture import CaptureTask, OperationHash, TaskId
from sourcefolio.domain.common import Provenance
from sourcefolio.domain.source import CaptureId


class CaptureAdapter(Protocol):
    @property
    def operation_key(self) -> str:
        """Stable resume/cache key for this operation, not merely a package version."""
        ...

    @property
    def provenance(self) -> Provenance: ...

    async def capture(self, task: CaptureTask) -> AdapterCapture:
        """Capture one item; clean up owned resources if raising."""
        ...


class CaptureStateStore(Protocol):
    def register(
        self,
        task: CaptureTask,
        operation_hash: OperationHash,
        now: datetime,
    ) -> bool:
        """Return False only when this exact operation already succeeded."""
        ...

    def claim(self, task_id: TaskId, operation_hash: OperationHash, now: datetime) -> bool: ...

    def start_attempt(
        self,
        task_id: TaskId,
        operation_hash: OperationHash,
        started_at: datetime,
    ) -> int: ...

    def finish_attempt(
        self,
        attempt_id: int,
        status: AttemptStatus,
        finished_at: datetime,
        error: str | None = None,
    ) -> None: ...

    def finish_job(
        self,
        task_id: TaskId,
        operation_hash: OperationHash,
        status: CaptureJobStatus,
        finished_at: datetime,
        capture_id: CaptureId | None = None,
        error: str | None = None,
    ) -> None: ...

    def get(self, task_id: TaskId) -> CaptureJob | None: ...

    def attempts(self, task_id: TaskId) -> tuple[CaptureAttempt, ...]: ...


class CaptureReportStore(Protocol):
    def iter_jobs(self) -> Iterator[tuple[CaptureJob, CaptureTask]]: ...

    def iter_attempts(self) -> Iterator[CaptureAttempt]: ...
