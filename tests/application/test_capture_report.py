from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evidence_vault.adapters.sqlite import SQLiteCaptureState
from evidence_vault.application.capture_report import (
    CaptureReportBuilder,
    render_capture_report_markdown,
)
from evidence_vault.application.capture_types import (
    AttemptStatus,
    CaptureJobStatus,
)
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.source import SourceIdentity, SourceKind

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def task(external_id: str, kind: SourceKind) -> CaptureTask:
    return CaptureTask.create(
        source=SourceIdentity(
            platform="test",
            kind=kind,
            external_id=external_id,
        ),
        raw_entries=({"id": external_id},),
    )


def test_capture_report_aggregates_status_retries_durations_and_errors(
    tmp_path: Path,
) -> None:
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        succeeded = task("ok", SourceKind.VIDEO)
        operation = succeeded.operation_hash("test/v1")
        state.register(succeeded, operation, NOW)
        state.claim(succeeded.task_id, operation, NOW)
        first = state.start_attempt(succeeded.task_id, operation, NOW)
        state.finish_attempt(
            first,
            AttemptStatus.RETRYING,
            NOW + timedelta(seconds=1),
            "retryable",
        )
        second = state.start_attempt(
            succeeded.task_id,
            operation,
            NOW + timedelta(seconds=2),
        )
        state.finish_attempt(second, AttemptStatus.SUCCEEDED, NOW + timedelta(seconds=5))
        state.finish_job(
            succeeded.task_id,
            operation,
            CaptureJobStatus.SUCCEEDED,
            NOW + timedelta(seconds=5),
        )

        failed = task("bad", SourceKind.SLIDESHOW)
        failed_operation = failed.operation_hash("test/v1")
        state.register(failed, failed_operation, NOW)
        state.claim(failed.task_id, failed_operation, NOW)
        attempt = state.start_attempt(failed.task_id, failed_operation, NOW)
        state.finish_attempt(attempt, AttemptStatus.FAILED, NOW + timedelta(seconds=4))
        state.finish_job(
            failed.task_id,
            failed_operation,
            CaptureJobStatus.FAILED,
            NOW + timedelta(seconds=4),
            error="safe failure",
        )

        running = task("running", SourceKind.OTHER)
        running_operation = running.operation_hash("test/v1")
        state.register(running, running_operation, NOW - timedelta(hours=2))
        state.claim(running.task_id, running_operation, NOW - timedelta(hours=2))

        report = CaptureReportBuilder(
            state=state,
            clock=lambda: NOW,
            stale_after_seconds=60,
        ).build()

    assert report.total_jobs == 3
    assert report.status_counts["succeeded"] == 1
    assert report.status_counts["failed"] == 1
    assert report.status_counts["running"] == 1
    assert report.kind_status_counts["video"]["succeeded"] == 1
    assert report.total_attempts == 3
    assert report.jobs_with_retries == 1
    assert report.retry_attempts == 1
    assert report.stale_running_candidates == 1
    assert report.attempt_duration.count == 3
    assert report.attempt_duration.mean_seconds == pytest.approx(8 / 3)
    assert report.attempt_duration.p50_seconds == 3
    assert report.attempt_duration.p95_seconds == pytest.approx(3.9)
    assert report.recent_errors[0].error == "safe failure"
    markdown = render_capture_report_markdown(report)
    assert "Jobs with retries: **1**" in markdown
    assert "safe failure" in markdown
