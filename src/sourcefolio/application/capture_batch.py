from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from io import BytesIO

from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.capture_ports import CaptureAdapter, CaptureStateStore
from sourcefolio.application.capture_types import (
    AdapterCapture,
    AttemptStatus,
    BatchItemResult,
    BatchItemStatus,
    CaptureError,
    CaptureJobStatus,
    RetryPolicy,
)
from sourcefolio.application.commands import (
    ArchiveCaptureCommand,
    ArchiveCaptureResult,
    ArtifactPayload,
)
from sourcefolio.domain.capture import CaptureTask, OperationHash
from sourcefolio.domain.common import canonical_json
from sourcefolio.domain.source import ArtifactRole, CaptureStatus

_QUEUE_MULTIPLIER = 2
_MAX_ERROR_LENGTH = 2000


def _safe_text(value: str) -> str:
    return " ".join(value.split())[:_MAX_ERROR_LENGTH]


class CaptureBatchRunner:
    """Bounded async adapter work + bounded thread offloading for filesystem work."""

    def __init__(
        self,
        *,
        adapter: CaptureAdapter,
        archive: ArchiveCapture,
        state: CaptureStateStore,
        concurrency: int = 4,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._adapter = adapter
        self._archive = archive
        self._state = state
        self._concurrency = concurrency
        self._retry = retry_policy or RetryPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._random = random_source or random.Random()

    async def run(self, tasks: Sequence[CaptureTask]) -> tuple[BatchItemResult, ...]:
        prepared_tasks = self._prepare(tasks)
        queue: asyncio.Queue[tuple[int, CaptureTask, OperationHash] | None] = asyncio.Queue(
            maxsize=self._concurrency * _QUEUE_MULTIPLIER
        )
        results: dict[int, BatchItemResult] = {}

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    index, task, operation_hash = item
                    results[index] = await self._process(task, operation_hash)
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker(), name=f"capture-worker-{index}")
            for index in range(self._concurrency)
        ]
        try:
            try:
                for index, (task, operation_hash) in enumerate(prepared_tasks):
                    if not self._state.register(task, operation_hash, self._clock()):
                        job = self._state.get(task.task_id)
                        results[index] = BatchItemResult(
                            task_id=task.task_id,
                            source_id=task.source_id,
                            status=BatchItemStatus.SKIPPED,
                            capture_id=job.capture_id if job else None,
                        )
                        continue
                    await queue.put((index, task, operation_hash))
            finally:
                for _ in workers:
                    await queue.put(None)
            await asyncio.gather(*workers)
        finally:
            for worker_task in workers:
                worker_task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        return tuple(results[index] for index in range(len(prepared_tasks)))

    def _prepare(
        self,
        tasks: Sequence[CaptureTask],
    ) -> tuple[tuple[CaptureTask, OperationHash], ...]:
        seen: dict[str, str] = {}
        prepared: list[tuple[CaptureTask, OperationHash]] = []
        for task in tasks:
            # Hash potentially large raw entries once, then reuse the resume key everywhere.
            input_hash = task.input_hash
            previous = seen.get(task.task_id)
            if previous is not None:
                if previous != input_hash:
                    raise ValueError(
                        "duplicate task_id with different input; merge tasks before running"
                    )
                continue
            seen[task.task_id] = input_hash
            operation_hash = task.operation_hash(
                self._adapter.operation_key,
                input_hash=input_hash,
            )
            prepared.append((task, operation_hash))
        return tuple(prepared)

    async def _process(
        self,
        task: CaptureTask,
        operation_hash: OperationHash,
    ) -> BatchItemResult:
        if not self._state.claim(task.task_id, operation_hash, self._clock()):
            job = self._state.get(task.task_id)
            return BatchItemResult(
                task_id=task.task_id,
                source_id=task.source_id,
                status=BatchItemStatus.SKIPPED,
                capture_id=job.capture_id if job else None,
            )

        for attempt_number in range(1, self._retry.max_attempts + 1):
            attempt_id = self._state.start_attempt(
                task.task_id,
                operation_hash,
                self._clock(),
            )
            try:
                capture = await self._adapter.capture(task)
            except Exception as exc:
                error, retryable = self._adapter_error(exc)
                if retryable and attempt_number < self._retry.max_attempts:
                    self._state.finish_attempt(
                        attempt_id,
                        AttemptStatus.RETRYING,
                        self._clock(),
                        error,
                    )
                    delay = self._retry.delay(attempt_number, self._random)
                    if delay:
                        await asyncio.sleep(delay)
                    continue

                self._state.finish_attempt(
                    attempt_id,
                    AttemptStatus.FAILED,
                    self._clock(),
                    error,
                )
                return await self._archive_fallback(task, operation_hash, error)

            try:
                archived = await self._archive_adapter_capture(task, capture)
            except Exception as exc:
                error = f"{type(exc).__name__} while archiving capture"
                self._state.finish_attempt(
                    attempt_id,
                    AttemptStatus.FAILED,
                    self._clock(),
                    error,
                )
                self._state.finish_job(
                    task.task_id,
                    operation_hash,
                    CaptureJobStatus.FAILED,
                    self._clock(),
                    error=error,
                )
                return BatchItemResult(
                    task_id=task.task_id,
                    source_id=task.source_id,
                    status=BatchItemStatus.FAILED,
                    error=error,
                )

            succeeded = capture.satisfied
            job_status = CaptureJobStatus.SUCCEEDED if succeeded else CaptureJobStatus.PARTIAL
            attempt_status = AttemptStatus.SUCCEEDED if succeeded else AttemptStatus.PARTIAL
            outcome_error = None
            if not succeeded:
                outcome_error = _safe_text("; ".join(capture.limitations)) or (
                    "adapter requires a follow-up capture"
                )
            self._state.finish_attempt(
                attempt_id,
                attempt_status,
                self._clock(),
                outcome_error,
            )
            self._state.finish_job(
                task.task_id,
                operation_hash,
                job_status,
                self._clock(),
                capture_id=archived.packet.capture_id,
                error=outcome_error,
            )
            return BatchItemResult(
                task_id=task.task_id,
                source_id=archived.packet.source_id,
                status=BatchItemStatus.SUCCEEDED if succeeded else BatchItemStatus.PARTIAL,
                capture_id=archived.packet.capture_id,
                error=outcome_error,
            )

        raise AssertionError("retry loop exited unexpectedly")

    async def _archive_fallback(
        self,
        task: CaptureTask,
        operation_hash: OperationHash,
        download_error: str,
    ) -> BatchItemResult:
        fallback = AdapterCapture(
            source=task.source,
            captured_at=self._clock(),
            limitations=(f"media capture failed: {download_error}",),
            satisfied=False,
        )
        try:
            archived = await self._archive_adapter_capture(task, fallback)
        except Exception as exc:
            error = f"{type(exc).__name__} while archiving fallback"
            self._state.finish_job(
                task.task_id,
                operation_hash,
                CaptureJobStatus.FAILED,
                self._clock(),
                error=error,
            )
            return BatchItemResult(
                task_id=task.task_id,
                source_id=task.source_id,
                status=BatchItemStatus.FAILED,
                error=error,
            )

        self._state.finish_job(
            task.task_id,
            operation_hash,
            CaptureJobStatus.PARTIAL,
            self._clock(),
            capture_id=archived.packet.capture_id,
            error=download_error,
        )
        return BatchItemResult(
            task_id=task.task_id,
            source_id=archived.packet.source_id,
            status=BatchItemStatus.PARTIAL,
            capture_id=archived.packet.capture_id,
            error=download_error,
        )

    async def _archive_adapter_capture(
        self,
        task: CaptureTask,
        capture: AdapterCapture,
    ) -> ArchiveCaptureResult:
        metadata_payload = ArtifactPayload(
            role=ArtifactRole.AUTHOR_METADATA,
            media_type="application/vnd.sourcefolio.capture-task+json",
            stream=BytesIO((canonical_json(task) + "\n").encode("utf-8")),
            original_filename="capture-task.json",
        )
        limitations = capture.limitations
        if not capture.artifacts and not limitations:
            limitations = ("adapter returned no media artifacts",)
        if not capture.satisfied and not limitations:
            limitations = ("adapter requires a follow-up capture",)
        status = (
            CaptureStatus.COMPLETE
            if capture.artifacts and not limitations
            else CaptureStatus.PARTIAL
        )
        command = ArchiveCaptureCommand(
            source=capture.source,
            captured_at=capture.captured_at,
            collector=self._adapter.provenance,
            artifacts=(*capture.artifacts, metadata_payload),
            status=status,
            title=capture.title,
            collections=task.collections,
            limitations=limitations,
            metadata={
                "task_id": task.task_id,
                "saved_index": task.metadata,
                "adapter": dict(capture.metadata),
            },
        )
        try:
            return await asyncio.to_thread(self._archive.handle, command)
        finally:
            metadata_payload.stream.close()
            capture.close()

    @staticmethod
    def _adapter_error(exc: Exception) -> tuple[str, bool]:
        if isinstance(exc, CaptureError):
            return _safe_text(str(exc)), exc.retryable
        return f"unexpected {type(exc).__name__} in capture adapter", False
