from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pytest

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.extractions import FileSystemExtractionRepository
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.extraction_types import ExtractedText, ExtractionError
from sourcefolio.application.ocr_backlog import OcrBacklogQuery, OcrBacklogRunner
from sourcefolio.application.ocr_backlog_report import (
    OcrBacklogReportDocument,
    build_ocr_backlog_report,
)
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.extraction import EvidenceLocator, ExtractionKind
from sourcefolio.domain.source import (
    ArtifactRef,
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeOcrAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.prepared = 0

    @property
    def provenance(self) -> Provenance:
        return Provenance(
            actor_kind=ActorKind.TOOL,
            actor_id="fake-ocr",
            method="test",
            version="1",
        )

    @property
    def config(self) -> dict[str, str]:
        return {"language": "eng"}

    @property
    def kind(self) -> ExtractionKind:
        return ExtractionKind.OCR_TEXT

    def prepare(self) -> None:
        self.prepared += 1

    def extract(
        self,
        *,
        artifact: ArtifactRef,
        content: BinaryIO,
        locator: EvidenceLocator,
    ) -> ExtractedText:
        self.calls += 1
        value = content.read().decode("utf-8")
        if value == "bad":
            raise ExtractionError("bad image")
        return ExtractedText(text=f"text:{value}")


class InterruptingOcrAdapter(FakeOcrAdapter):
    def extract(
        self,
        *,
        artifact: ArtifactRef,
        content: BinaryIO,
        locator: EvidenceLocator,
    ) -> ExtractedText:
        if content.read().decode("utf-8") == "interrupt":
            self.calls += 1
            raise KeyboardInterrupt
        content.seek(0)
        return super().extract(artifact=artifact, content=content, locator=locator)


def add_packet(
    root: Path,
    external_id: str,
    images: tuple[str, ...],
    *,
    captured_at: datetime = NOW,
    collection: str = "Ideas",
) -> SourcePacket:
    return (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="test",
                    kind=SourceKind.SLIDESHOW,
                    external_id=external_id,
                ),
                captured_at=captured_at,
                collector=Provenance(
                    actor_kind=ActorKind.TOOL,
                    actor_id="test",
                    method="test",
                ),
                collections=(CollectionMembership(name=collection),),
                artifacts=tuple(
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_IMAGE,
                        media_type="image/jpeg",
                        stream=BytesIO(image.encode("utf-8")),
                        original_filename=f"slide-{index}.jpg",
                    )
                    for index, image in enumerate(images, start=1)
                ),
            )
        )
        .packet
    )


def runner(root: Path, adapter: FakeOcrAdapter) -> OcrBacklogRunner:
    return OcrBacklogRunner(
        packets=FileSystemSourcePacketRepository(root),
        artifacts=FileSystemArtifactStore(root),
        extractions=FileSystemExtractionRepository(root / "derived"),
        adapter=adapter,
        concurrency=2,
        clock=lambda: NOW,
    )


def test_backlog_is_bounded_resumable_and_reports_metrics(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", ("a", "b"))
    add_packet(tmp_path, "two", ("c", "d"))
    adapter = FakeOcrAdapter()
    query = OcrBacklogQuery(capture_limit=10, slide_limit=10, latest_only=False)

    first = runner(tmp_path, adapter).run(query)
    second = runner(tmp_path, adapter).run(query)

    assert first.matched_captures == 2
    assert first.selected_captures == 2
    assert first.total_images == 4
    assert first.created == 4
    assert first.failed == 0
    assert first.item_duration.count == 4
    assert second.skipped == 4
    assert second.created == 0
    assert adapter.calls == 4

    document = build_ocr_backlog_report(
        operation="ocr-backlog",
        query=query,
        report=first,
        adapter=adapter,
        runtime={"timeout_seconds": 60},
    )
    assert document.counts.created == 4
    assert document.selection.selected_images == 4
    assert document.concurrency == 2
    assert document.extractor.version == "1"
    assert all(item.request_id for item in document.items)
    OcrBacklogReportDocument.model_validate(document.model_dump(mode="json"))


def test_backlog_uses_latest_capture_per_source_and_collection_filter(
    tmp_path: Path,
) -> None:
    older = add_packet(tmp_path, "one", ("old",), captured_at=NOW)
    newer = add_packet(
        tmp_path,
        "one",
        ("new",),
        captured_at=NOW + timedelta(minutes=1),
    )
    other = add_packet(tmp_path, "two", ("other",), collection="Films")

    report = runner(tmp_path, FakeOcrAdapter()).run(
        OcrBacklogQuery(collection="ideas", latest_only=True)
    )

    assert report.matched_captures == 1
    assert report.created == 1
    assert report.results[0].capture_id == newer.capture_id
    assert report.results[0].capture_id != older.capture_id
    assert other.capture_id not in {item.capture_id for item in report.results}


def test_backlog_isolates_failures_and_retries_only_failed_work(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", ("good", "bad", "fine"))
    adapter = FakeOcrAdapter()
    query = OcrBacklogQuery(capture_limit=1, slide_limit=3, latest_only=False)

    first = runner(tmp_path, adapter).run(query)
    second = runner(tmp_path, adapter).run(query)

    assert (first.created, first.skipped, first.failed) == (2, 0, 1)
    assert [item.slide for item in first.results] == [1, 2, 3]
    assert first.results[1].error == "bad image"
    assert (second.created, second.skipped, second.failed) == (0, 2, 1)
    assert adapter.calls == 4


def test_backlog_resumes_completed_items_after_worker_interruption(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", ("good", "interrupt", "fine"))
    query = OcrBacklogQuery(capture_limit=1, slide_limit=3, latest_only=False)

    with pytest.raises(KeyboardInterrupt):
        runner(tmp_path, InterruptingOcrAdapter()).run(query)

    resumed = runner(tmp_path, FakeOcrAdapter()).run(query)
    assert resumed.failed == 0
    assert resumed.created + resumed.skipped == 3
    assert resumed.skipped >= 1
    records = tuple(
        FileSystemExtractionRepository(tmp_path / "derived").iter_for_capture(packet.capture_id)
    )
    assert len(records) == 3
