from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.capture_types import AdapterCapture, CaptureError
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.cover_backfill import (
    CoverBackfillQuery,
    CoverBackfillRunner,
)
from sourcefolio.domain.capture import CaptureTask
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CaptureStatus,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeAdapter:
    def __init__(
        self,
        *,
        image: bytes | None = b"cover-bytes",
        error: Exception | None = None,
    ) -> None:
        self._image = image
        self._error = error
        self.calls: list[str] = []

    @property
    def operation_key(self) -> str:
        return "fake/v1"

    @property
    def provenance(self) -> Provenance:
        return Provenance(actor_kind=ActorKind.TOOL, actor_id="fake", method="fake")

    async def capture(self, task: CaptureTask) -> AdapterCapture:
        self.calls.append(task.source.external_id or "")
        if self._error is not None:
            raise self._error
        artifacts = ()
        if self._image is not None:
            artifacts = (
                ArtifactPayload(
                    role=ArtifactRole.ORIGINAL_IMAGE,
                    media_type="image/jpeg",
                    stream=BytesIO(self._image),
                    original_filename="cover.jpg",
                ),
            )
        return AdapterCapture(
            source=task.source,
            captured_at=NOW,
            artifacts=artifacts,
            limitations=("cover-only capture: video was not downloaded",),
            satisfied=False,
        )


def add_packet(
    root: Path,
    external_id: str,
    *,
    collection: str = "Нейронки",
    kind: SourceKind = SourceKind.VIDEO,
    with_image: bool = False,
    captured_at: datetime = NOW,
) -> SourcePacket:
    artifacts = [
        ArtifactPayload(
            role=ArtifactRole.ORIGINAL_VIDEO,
            media_type="video/mp4",
            stream=BytesIO(b"video-bytes"),
        )
    ]
    if with_image:
        artifacts.append(
            ArtifactPayload(
                role=ArtifactRole.ORIGINAL_IMAGE,
                media_type="image/jpeg",
                stream=BytesIO(b"existing-cover"),
            )
        )
    return (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="tiktok",
                    kind=kind,
                    external_id=external_id,
                    canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
                ),
                captured_at=captured_at,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                collections=(CollectionMembership(name=collection),),
                artifacts=tuple(artifacts),
                status=CaptureStatus.COMPLETE,
                metadata={"saved_index": {"desc": "текст"}},
            )
        )
        .packet
    )


def runner(
    root: Path,
    videos: FakeAdapter,
    slideshows: FakeAdapter | None = None,
) -> CoverBackfillRunner:
    packets = FileSystemSourcePacketRepository(root)
    return CoverBackfillRunner(
        packets=packets,
        artifacts=FileSystemArtifactStore(root),
        archive=ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=packets,
        ),
        videos=videos,
        slideshows=slideshows or FakeAdapter(),
    )


def backfilled_packets(root: Path) -> list[SourcePacket]:
    return [
        packet
        for packet in FileSystemSourcePacketRepository(root).iter_packets()
        if packet.collector.actor_id == "cover-backfill"
    ]


def test_cover_backfill_adds_image_to_the_latest_capture(tmp_path: Path) -> None:
    original = add_packet(tmp_path, "one")
    videos = FakeAdapter()

    report = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery()))

    assert report.selected == 1
    assert (report.created, report.skipped, report.failed) == (1, 0, 0)
    assert videos.calls == ["one"]
    packets = backfilled_packets(tmp_path)
    assert len(packets) == 1
    packet = packets[0]
    roles = [artifact.role for artifact in packet.artifacts]
    assert roles.count(ArtifactRole.ORIGINAL_VIDEO) == 1
    assert roles.count(ArtifactRole.ORIGINAL_IMAGE) == 1
    assert packet.metadata["cover_backfill"] == {"from_capture_id": original.capture_id}


def test_cover_backfill_keeps_previous_artifacts_and_metadata(tmp_path: Path) -> None:
    original = add_packet(tmp_path, "one")
    videos = FakeAdapter()

    asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery()))

    packet = backfilled_packets(tmp_path)[0]
    assert packet.source_id == original.source_id
    assert packet.captured_at == original.captured_at
    assert packet.title == original.title
    assert packet.collections == original.collections
    assert packet.metadata["saved_index"] == {"desc": "текст"}


def test_cover_backfill_skips_sources_that_already_have_an_image(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", with_image=True)
    videos = FakeAdapter()

    report = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery()))

    assert (report.created, report.skipped, report.failed) == (0, 1, 0)
    assert videos.calls == []
    assert backfilled_packets(tmp_path) == []


def test_cover_backfill_include_existing_reprocesses_the_source(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", with_image=True)
    videos = FakeAdapter()

    report = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery(skip_existing=False)))

    assert report.created == 1
    assert videos.calls == ["one"]


def test_cover_backfill_filters_by_collection_and_kind(tmp_path: Path) -> None:
    add_packet(tmp_path, "video", collection="Нейронки")
    add_packet(tmp_path, "other", collection="Другое")
    videos = FakeAdapter()

    report = asyncio.run(
        runner(tmp_path, videos).run(CoverBackfillQuery(collections=("нейронки",)))
    )

    assert report.selected == 1
    assert videos.calls == ["video"]


def test_cover_backfill_respects_limit(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    add_packet(tmp_path, "two")
    videos = FakeAdapter()

    report = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery(limit=1)))

    assert report.selected == 1
    assert len(videos.calls) == 1


def test_cover_backfill_reports_adapter_failures_without_raising(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    videos = FakeAdapter(error=CaptureError("yt-dlp exited with status 1"))

    report = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery()))

    assert (report.created, report.skipped, report.failed) == (0, 0, 1)
    assert report.results[0].reason == "yt-dlp exited with status 1"
    assert backfilled_packets(tmp_path) == []


def test_cover_backfill_fails_when_adapter_returns_no_image(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    videos = FakeAdapter(image=None)

    report = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery()))

    assert report.failed == 1
    assert report.results[0].reason == "adapter returned no image artifact"
    assert backfilled_packets(tmp_path) == []


def test_cover_backfill_rerun_is_idempotent(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    videos = FakeAdapter()

    first = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery()))
    second = asyncio.run(runner(tmp_path, videos).run(CoverBackfillQuery()))

    assert (first.created, first.skipped) == (1, 0)
    assert (second.created, second.skipped) == (0, 1)
    assert len(backfilled_packets(tmp_path)) == 1
