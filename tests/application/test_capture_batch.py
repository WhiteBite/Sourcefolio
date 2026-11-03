from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.metadata_only import MetadataOnlyCaptureAdapter
from evidence_vault.adapters.sqlite import SQLiteCaptureState
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.capture_batch import CaptureBatchRunner
from evidence_vault.application.capture_types import (
    AdapterCapture,
    AttemptStatus,
    BatchItemStatus,
    CaptureError,
    CaptureJobStatus,
    RetryPolicy,
)
from evidence_vault.application.commands import ArtifactPayload
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CaptureStatus,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PROVENANCE = Provenance(
    actor_kind=ActorKind.TOOL,
    actor_id="fake-adapter",
    method="test",
    version="1",
)


def make_task(external_id: str, *, extra: str | None = None) -> CaptureTask:
    raw = {"Link": f"https://www.tiktok.com/@x/video/{external_id}"}
    if extra is not None:
        raw["Extra"] = extra
    return CaptureTask.create(
        source=SourceIdentity(
            platform="tiktok",
            kind=SourceKind.VIDEO,
            external_id=external_id,
            canonical_url=raw["Link"],
        ),
        raw_entries=(raw,),
    )


class FakeAdapter:
    operation_key = "fake-capture/v1"
    provenance = PROVENANCE

    def __init__(
        self,
        *,
        failures: dict[str, list[Exception]] | None = None,
        delay: float = 0,
    ) -> None:
        self.failures = failures or {}
        self.delay = delay
        self.calls: dict[str, int] = {}
        self.active = 0
        self.max_active = 0
        self.streams: list[BytesIO] = []
        self.cleanup_count = 0

    async def capture(self, task: CaptureTask) -> AdapterCapture:
        external_id = task.source.external_id or "missing"
        self.calls[external_id] = self.calls.get(external_id, 0) + 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.failures.get(external_id):
                raise self.failures[external_id].pop(0)
            stream = BytesIO(f"media-{external_id}".encode())
            self.streams.append(stream)

            def cleanup() -> None:
                self.cleanup_count += 1

            return AdapterCapture(
                source=task.source,
                captured_at=NOW,
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_VIDEO,
                        media_type="video/mp4",
                        stream=stream,
                        original_filename=f"{external_id}.mp4",
                    ),
                ),
                cleanup=cleanup,
            )
        finally:
            self.active -= 1


def build_archive(root: Path) -> ArchiveCapture:
    return ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    )


def build_runner(
    adapter,
    archive: ArchiveCapture,
    state: SQLiteCaptureState,
    *,
    concurrency: int = 3,
    max_attempts: int = 3,
) -> CaptureBatchRunner:
    return CaptureBatchRunner(
        adapter=adapter,
        archive=archive,
        state=state,
        concurrency=concurrency,
        retry_policy=RetryPolicy(
            max_attempts=max_attempts,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter_ratio=0,
        ),
        clock=lambda: NOW,
    )


def test_worker_pool_is_parallel_but_never_exceeds_limit(tmp_path: Path) -> None:
    tasks = [make_task(str(index)) for index in range(9)]
    adapter = FakeAdapter(delay=0.02)
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        runner = build_runner(adapter, build_archive(tmp_path / "archive"), state)
        results = asyncio.run(runner.run(tasks))

        assert len(results) == len(tasks)
        assert all(result.status == BatchItemStatus.SUCCEEDED for result in results)
        assert 2 <= adapter.max_active <= 3
        assert all(state.get(task.task_id).status == CaptureJobStatus.SUCCEEDED for task in tasks)


def test_successful_operations_are_skipped_on_resume(tmp_path: Path) -> None:
    task = make_task("123")
    archive = build_archive(tmp_path / "archive")
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        first_adapter = FakeAdapter()
        first = asyncio.run(build_runner(first_adapter, archive, state).run([task]))
        second_adapter = FakeAdapter()
        second = asyncio.run(build_runner(second_adapter, archive, state).run([task]))

        assert first[0].status == BatchItemStatus.SUCCEEDED
        assert second[0].status == BatchItemStatus.SKIPPED
        assert second[0].capture_id == first[0].capture_id
        assert second_adapter.calls == {}


def test_retryable_failure_uses_bounded_retries_and_records_attempts(tmp_path: Path) -> None:
    task = make_task("123")
    adapter = FakeAdapter(
        failures={
            "123": [
                CaptureError("temporary one", retryable=True),
                CaptureError("temporary two", retryable=True),
            ]
        }
    )
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        result = asyncio.run(build_runner(adapter, build_archive(tmp_path), state).run([task]))[0]
        attempts = state.attempts(task.task_id)

        assert result.status == BatchItemStatus.SUCCEEDED
        assert adapter.calls["123"] == 3
        assert [attempt.status for attempt in attempts] == [
            AttemptStatus.RETRYING,
            AttemptStatus.RETRYING,
            AttemptStatus.SUCCEEDED,
        ]


def test_failed_media_capture_falls_back_to_partial_and_can_resume(tmp_path: Path) -> None:
    task = make_task("123")
    archive = build_archive(tmp_path / "archive")
    artifact_store = FileSystemArtifactStore(tmp_path / "archive")
    packets = FileSystemSourcePacketRepository(tmp_path / "archive")
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        failing = FakeAdapter(failures={"123": [CaptureError("private video", retryable=False)]})
        first = asyncio.run(build_runner(failing, archive, state).run([task]))[0]

        assert first.status == BatchItemStatus.PARTIAL
        packet = packets.get(first.source_id, first.capture_id)
        assert packet is not None
        assert packet.status == CaptureStatus.PARTIAL
        assert len(packet.artifacts) == 1
        assert packet.artifacts[0].role == ArtifactRole.AUTHOR_METADATA
        with artifact_store.open(packet.artifacts[0].artifact_id) as stream:
            assert b"capture-task/v1" in stream.read()

        succeeding = FakeAdapter()
        second = asyncio.run(build_runner(succeeding, archive, state).run([task]))[0]
        assert second.status == BatchItemStatus.SUCCEEDED
        assert state.get(task.task_id).status == CaptureJobStatus.SUCCEEDED


def test_one_bad_item_does_not_cancel_the_batch_or_leak_exception_text(
    tmp_path: Path,
) -> None:
    good = make_task("good")
    bad = make_task("bad")
    adapter = FakeAdapter(failures={"bad": [ValueError("secret cookie must not be persisted")]})
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        results = asyncio.run(
            build_runner(adapter, build_archive(tmp_path), state).run([good, bad])
        )

        assert results[0].status == BatchItemStatus.SUCCEEDED
        assert results[1].status == BatchItemStatus.PARTIAL
        assert results[1].error == "unexpected ValueError in capture adapter"
        assert "secret" not in (state.get(bad.task_id).last_error or "")


def test_metadata_only_success_is_distinct_from_capture_completeness(tmp_path: Path) -> None:
    task = make_task("123")
    adapter = MetadataOnlyCaptureAdapter(clock=lambda: NOW)
    archive = build_archive(tmp_path / "archive")
    packets = FileSystemSourcePacketRepository(tmp_path / "archive")
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        runner = build_runner(adapter, archive, state)
        first = asyncio.run(runner.run([task]))[0]
        second = asyncio.run(runner.run([task]))[0]

        assert first.status == BatchItemStatus.SUCCEEDED
        assert second.status == BatchItemStatus.SKIPPED
        packet = packets.get(first.source_id, first.capture_id)
        assert packet is not None
        assert packet.status == CaptureStatus.PARTIAL


def test_cleanup_runs_after_threaded_archiving(tmp_path: Path) -> None:
    task = make_task("123")
    adapter = FakeAdapter()
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        result = asyncio.run(build_runner(adapter, build_archive(tmp_path), state).run([task]))[0]

        assert result.status == BatchItemStatus.SUCCEEDED
        assert adapter.cleanup_count == 1
        assert len(adapter.streams) == 1
        assert adapter.streams[0].closed


def test_conflicting_duplicate_tasks_fail_before_any_work(tmp_path: Path) -> None:
    first = make_task("123", extra="one")
    second = make_task("123", extra="two")
    adapter = FakeAdapter()
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        runner = build_runner(adapter, build_archive(tmp_path), state)
        with pytest.raises(ValueError, match="duplicate task_id"):
            asyncio.run(runner.run([first, second]))

        assert adapter.calls == {}
        assert state.get(first.task_id) is None
