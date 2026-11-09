from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sourcefolio.application.capture_ports import CaptureReportStore
from sourcefolio.application.capture_types import (
    AttemptStatus,
    CaptureJobStatus,
)
from sourcefolio.application.metrics import DurationSummary, summarize_durations
from sourcefolio.domain.capture import TaskId
from sourcefolio.domain.source import CaptureId, SourceKind


@dataclass(frozen=True)
class CaptureErrorSummary:
    task_id: TaskId
    capture_id: CaptureId | None
    external_id: str | None
    canonical_url: str | None
    kind: SourceKind
    status: CaptureJobStatus
    error: str
    updated_at: datetime


@dataclass(frozen=True)
class CaptureReport:
    generated_at: datetime
    total_jobs: int
    status_counts: dict[str, int]
    kind_status_counts: dict[str, dict[str, int]]
    total_attempts: int
    attempt_status_counts: dict[str, int]
    jobs_with_retries: int
    retry_attempts: int
    stale_running_candidates: int
    attempt_duration: DurationSummary
    recent_errors: tuple[CaptureErrorSummary, ...]


class CaptureReportBuilder:
    def __init__(
        self,
        *,
        state: CaptureReportStore,
        clock: Callable[[], datetime] | None = None,
        stale_after_seconds: float = 3600,
        recent_errors_limit: int = 20,
    ) -> None:
        if stale_after_seconds < 0 or recent_errors_limit < 0:
            raise ValueError("report thresholds cannot be negative")
        self._state = state
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stale_after = stale_after_seconds
        self._errors_limit = recent_errors_limit

    def build(self) -> CaptureReport:
        now = self._clock()
        status_counts = {status.value: 0 for status in CaptureJobStatus}
        attempt_status_counts = {status.value: 0 for status in AttemptStatus}
        kind_status_counts: dict[str, dict[str, int]] = {}
        errors: list[CaptureErrorSummary] = []
        stale_running = 0
        total_jobs = 0

        for job, task in self._state.iter_jobs():
            total_jobs += 1
            status_counts[job.status.value] += 1
            kind = task.source.kind.value
            by_status = kind_status_counts.setdefault(
                kind,
                {status.value: 0 for status in CaptureJobStatus},
            )
            by_status[job.status.value] += 1
            if (
                job.status == CaptureJobStatus.RUNNING
                and (now - job.updated_at).total_seconds() >= self._stale_after
            ):
                stale_running += 1
            if job.status in {CaptureJobStatus.FAILED, CaptureJobStatus.PARTIAL}:
                errors.append(
                    CaptureErrorSummary(
                        task_id=job.task_id,
                        capture_id=job.capture_id,
                        external_id=task.source.external_id,
                        canonical_url=task.source.canonical_url,
                        kind=task.source.kind,
                        status=job.status,
                        error=job.last_error or "unspecified capture outcome",
                        updated_at=job.updated_at,
                    )
                )

        total_attempts = 0
        retry_attempts = 0
        retry_tasks: set[str] = set()
        durations: list[float] = []
        for attempt in self._state.iter_attempts():
            total_attempts += 1
            attempt_status_counts[attempt.status.value] += 1
            if attempt.status == AttemptStatus.RETRYING:
                retry_attempts += 1
                retry_tasks.add(attempt.task_id)
            if attempt.finished_at is not None:
                duration = (attempt.finished_at - attempt.started_at).total_seconds()
                if duration >= 0:
                    durations.append(duration)

        errors.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
        return CaptureReport(
            generated_at=now,
            total_jobs=total_jobs,
            status_counts=status_counts,
            kind_status_counts=kind_status_counts,
            total_attempts=total_attempts,
            attempt_status_counts=attempt_status_counts,
            jobs_with_retries=len(retry_tasks),
            retry_attempts=retry_attempts,
            stale_running_candidates=stale_running,
            attempt_duration=summarize_durations(durations),
            recent_errors=tuple(errors[: self._errors_limit]),
        )


def render_capture_report_markdown(report: CaptureReport) -> str:
    lines = [
        "# Capture report",
        "",
        f"Generated: `{report.generated_at.isoformat()}`",
        "",
        "## Jobs",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    lines.extend(f"| {status} | {count} |" for status, count in report.status_counts.items())
    lines.extend(
        [
            "",
            f"- Total jobs: **{report.total_jobs}**",
            f"- Attempts: **{report.total_attempts}**",
            f"- Jobs with retries: **{report.jobs_with_retries}**",
            f"- Retry attempts: **{report.retry_attempts}**",
            f"- Stale running candidates: **{report.stale_running_candidates}**",
            "",
            "## Attempt duration (finished attempts)",
            "",
            "| Count | Mean | p50 | p95 | Max |",
            "|---:|---:|---:|---:|---:|",
            (
                f"| {report.attempt_duration.count} "
                f"| {report.attempt_duration.mean_seconds:.3f}s "
                f"| {report.attempt_duration.p50_seconds:.3f}s "
                f"| {report.attempt_duration.p95_seconds:.3f}s "
                f"| {report.attempt_duration.max_seconds:.3f}s |"
            ),
            "",
            "## Jobs by source kind",
            "",
            "| Kind | Status | Count |",
            "|---|---|---:|",
        ]
    )
    for kind, by_status in sorted(report.kind_status_counts.items()):
        for status, count in by_status.items():
            if count:
                lines.append(f"| {kind} | {status} | {count} |")
    lines.extend(["", "## Recent partial/failed jobs", ""])
    if not report.recent_errors:
        lines.append("None.")
    else:
        lines.extend(["| Kind | Status | External ID | Error |", "|---|---|---|---|"])
        for error in report.recent_errors:
            message = " ".join(error.error.split()).replace("|", "\\|")
            lines.append(
                f"| {error.kind.value} | {error.status.value} "
                f"| {error.external_id or '—'} | {message} |"
            )
    lines.append("")
    return "\n".join(lines)
