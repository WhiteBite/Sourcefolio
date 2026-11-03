from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue

from evidence_vault.application.commands import ArtifactPayload
from evidence_vault.domain.capture import OperationHash, TaskId
from evidence_vault.domain.source import CaptureId, SourceId, SourceIdentity


class CaptureJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class BatchItemStatus(StrEnum):
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class CaptureError(Exception):
    """An adapter error whose message is safe to persist and whose retry is explicit."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def delay(self, failed_attempt: int, random_source: random.Random) -> float:
        if failed_attempt < 1:
            raise ValueError("failed_attempt must be at least 1")
        exponential = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** (failed_attempt - 1)),
        )
        jitter = exponential * self.jitter_ratio
        random_offset = float(random_source.uniform(-jitter, jitter))
        return float(max(0.0, exponential + random_offset))


@dataclass
class AdapterCapture:
    """Adapter-owned resources are transferred to the batch runner until close()."""

    source: SourceIdentity
    captured_at: datetime
    artifacts: tuple[ArtifactPayload, ...] = ()
    title: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    satisfied: bool = True
    cleanup: Callable[[], None] | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        try:
            for artifact in self.artifacts:
                artifact.stream.close()
        finally:
            self._closed = True
            if self.cleanup is not None:
                self.cleanup()


@dataclass(frozen=True)
class BatchItemResult:
    task_id: TaskId
    source_id: SourceId
    status: BatchItemStatus
    capture_id: CaptureId | None = None
    error: str | None = None


@dataclass(frozen=True)
class CaptureJob:
    task_id: TaskId
    operation_hash: OperationHash
    status: CaptureJobStatus
    capture_id: CaptureId | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CaptureAttempt:
    attempt_id: int
    task_id: TaskId
    operation_hash: OperationHash
    attempt_number: int
    status: AttemptStatus
    error: str | None
    started_at: datetime
    finished_at: datetime | None
