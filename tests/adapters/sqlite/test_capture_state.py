from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from evidence_vault.adapters.sqlite import SQLiteCaptureState
from evidence_vault.application.capture_types import AttemptStatus, CaptureJobStatus
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.source import SourceIdentity, SourceKind

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def task() -> CaptureTask:
    return CaptureTask.create(
        source=SourceIdentity(
            platform="tiktok",
            kind=SourceKind.VIDEO,
            external_id="123",
        ),
        raw_entries=({"Link": "https://example.test"},),
    )


def test_running_job_and_attempt_are_recovered_after_restart(tmp_path: Path) -> None:
    path = Path(tmp_path / "control.sqlite")
    capture_task = task()
    operation_hash = capture_task.operation_hash("adapter/v1")

    first = SQLiteCaptureState(path)
    assert first.register(capture_task, operation_hash, NOW)
    assert first.claim(capture_task.task_id, operation_hash, NOW)
    attempt_id = first.start_attempt(capture_task.task_id, operation_hash, NOW)
    first.close()

    second = SQLiteCaptureState(path)
    assert second.register(capture_task, operation_hash, NOW)
    job = second.get(capture_task.task_id)
    attempt = second.attempts(capture_task.task_id)[0]
    assert job is not None
    assert job.status == CaptureJobStatus.PENDING
    assert attempt.attempt_id == attempt_id
    assert attempt.status == AttemptStatus.INTERRUPTED
    second.close()


def test_new_adapter_operation_does_not_skip_previous_success(tmp_path: Path) -> None:
    capture_task = task()
    old_operation = capture_task.operation_hash("metadata/v1")
    new_operation = capture_task.operation_hash("media/v1")
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        assert state.register(capture_task, old_operation, NOW)
        assert state.claim(capture_task.task_id, old_operation, NOW)
        state.finish_job(
            capture_task.task_id,
            old_operation,
            CaptureJobStatus.SUCCEEDED,
            NOW,
            capture_id="cap_" + "1" * 64,
        )

        assert not state.register(capture_task, old_operation, NOW)
        assert state.register(capture_task, new_operation, NOW)
        job = state.get(capture_task.task_id)
        assert job is not None
        assert job.status == CaptureJobStatus.PENDING
        assert job.operation_hash == new_operation
