from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.extractions import FileSystemExtractionRepository
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.ocr_backlog_report import (
    OcrBacklogCountsDocument,
    OcrBacklogItemDocument,
    OcrBacklogQueryDocument,
    OcrBacklogReportDocument,
    OcrBacklogSelectionDocument,
    OcrDurationDocument,
)
from sourcefolio.application.ocr_evaluation import (
    OcrEvaluationSample,
    OcrEvaluationSet,
    OcrEvaluator,
)
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.extraction import (
    EvidenceLocator,
    ExtractionKind,
    ExtractionRecord,
    ExtractionRequest,
    LocatorKind,
)
from sourcefolio.domain.source import (
    ArtifactRole,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
EXTRACTOR = Provenance(
    actor_kind=ActorKind.TOOL,
    actor_id="tesseract",
    method="test",
    version="test",
)


def make_packet(root: Path) -> SourcePacket:
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
                    external_id="ocr-evaluation",
                ),
                captured_at=NOW,
                collector=EXTRACTOR,
                artifacts=tuple(
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_IMAGE,
                        media_type="image/jpeg",
                        stream=BytesIO(f"image-{index}".encode()),
                    )
                    for index in range(1, 7)
                ),
            )
        )
        .packet
    )


def save_record(
    extractions: FileSystemExtractionRepository,
    packet: SourcePacket,
    slide: int,
    text: str,
    *,
    version: str = "test",
    created_at: datetime = NOW,
    limitations: tuple[str, ...] = (),
) -> ExtractionRecord:
    request = ExtractionRequest(
        source_id=packet.source_id,
        capture_id=packet.capture_id,
        artifact_id=packet.artifacts[slide - 1].artifact_id,
        kind=ExtractionKind.OCR_TEXT,
        locator=EvidenceLocator(kind=LocatorKind.SLIDE, slide=slide),
        extractor=Provenance(
            actor_kind=ActorKind.TOOL,
            actor_id="tesseract",
            method="test",
            version=version,
        ),
        config={"language": "eng"},
    )
    record = ExtractionRecord.create(
        request=request,
        created_at=created_at,
        text=text,
        limitations=limitations,
    )
    extractions.save(record)
    return record


def sample(packet: SourcePacket, slide: int, expected: str) -> OcrEvaluationSample:
    return OcrEvaluationSample(
        sample_id=f"sample-{slide}",
        capture_id=packet.capture_id,
        artifact_id=packet.artifacts[slide - 1].artifact_id,
        locator=EvidenceLocator(kind=LocatorKind.SLIDE, slide=slide),
        expected_text=expected,
    )


def failed_report(packet: SourcePacket) -> OcrBacklogReportDocument:
    item = OcrBacklogItemDocument(
        source_id=packet.source_id,
        capture_id=packet.capture_id,
        artifact_id=packet.artifacts[2].artifact_id,
        slide=3,
        status="failed",
        duration_seconds=0.1,
        error="synthetic timeout",
    )
    return OcrBacklogReportDocument(
        operation="ocr-backlog",
        started_at=NOW,
        finished_at=NOW,
        query=OcrBacklogQueryDocument(
            capture_limit=1,
            slide_limit=6,
            latest_only=True,
        ),
        concurrency=1,
        adapter_prepared=True,
        extractor=EXTRACTOR,
        selection=OcrBacklogSelectionDocument(
            matched_captures=1,
            selected_captures=1,
            total_images=6,
            selected_images=1,
        ),
        counts=OcrBacklogCountsDocument(created=0, skipped=0, failed=1),
        wall_seconds=0.1,
        item_duration_seconds=OcrDurationDocument(
            count=1,
            total=0.1,
            mean=0.1,
            p50=0.1,
            p95=0.1,
            max=0.1,
        ),
        items=(item,),
    )


def test_ocr_evaluation_scores_quality_and_classifies_unresolved_samples(
    tmp_path: Path,
) -> None:
    packet = make_packet(tmp_path / "archive")
    extractions = FileSystemExtractionRepository(tmp_path / "derived")
    save_record(extractions, packet, 1, "hello world")
    save_record(extractions, packet, 2, "adc")
    save_record(extractions, packet, 5, "", limitations=("empty OCR result",))
    save_record(extractions, packet, 6, "old", version="old")
    save_record(
        extractions,
        packet,
        6,
        "new",
        version="new",
        created_at=NOW + timedelta(seconds=1),
    )
    evaluation_set = OcrEvaluationSet(
        samples=(
            sample(packet, 1, "  Hello\nWORLD "),
            sample(packet, 2, "abc"),
            sample(packet, 3, "failed"),
            sample(packet, 4, "missing"),
            sample(packet, 5, "text"),
            sample(packet, 6, "new"),
        )
    )

    report = OcrEvaluator(
        extractions=extractions,
        clock=lambda: NOW,
    ).evaluate(evaluation_set, batch_reports=(failed_report(packet),))

    assert report.summary.sample_count == 6
    assert report.summary.evaluated == 4
    assert report.summary.failed == 1
    assert report.summary.missing == 1
    assert report.summary.empty_actual == 1
    assert report.summary.exact_matches == 2
    assert report.summary.exact_match_rate == 0.5
    assert report.summary.empty_actual_rate == 0.25
    assert report.summary.failed_rate == pytest.approx(1 / 6)
    assert report.summary.missing_rate == pytest.approx(1 / 6)
    assert report.summary.mean_cer == pytest.approx(1 / 3)
    assert report.summary.mean_wer == 0.5
    assert report.summary.multiple_record_samples == 1
    assert report.cases[0].normalized_exact is True
    assert report.cases[1].cer == pytest.approx(1 / 3)
    assert report.cases[1].wer == 1
    assert report.cases[2].error == "synthetic timeout"
    assert report.cases[4].extraction_limitations == ("empty OCR result",)
    assert report.cases[5].extractor is not None
    assert report.cases[5].extractor.version == "new"
    assert len(report.extractors) == 2
    assert len(report.batch_report_sha256) == 1


def test_ocr_evaluation_can_pin_request_and_does_not_guess_failures(
    tmp_path: Path,
) -> None:
    packet = make_packet(tmp_path / "archive")
    extractions = FileSystemExtractionRepository(tmp_path / "derived")
    old = save_record(extractions, packet, 6, "old", version="old")
    save_record(
        extractions,
        packet,
        6,
        "new",
        version="new",
        created_at=NOW + timedelta(seconds=1),
    )
    pinned = sample(packet, 6, "new").model_copy(update={"request_id": old.request_id})
    pinned_report = OcrEvaluator(extractions=extractions).evaluate(
        OcrEvaluationSet(samples=(pinned,))
    )
    assert pinned_report.cases[0].extraction_id == old.extraction_id
    assert pinned_report.cases[0].normalized_exact is False
    assert pinned_report.cases[0].available_records == 2

    unresolved = OcrEvaluator(extractions=extractions).evaluate(
        OcrEvaluationSet(samples=(sample(packet, 3, "failed"),))
    )
    assert unresolved.summary.failed == 0
    assert unresolved.summary.missing == 1
    assert any("cannot be classified" in item for item in unresolved.limitations)


def test_ocr_evaluation_marks_oversized_text_without_scoring(tmp_path: Path) -> None:
    packet = make_packet(tmp_path / "archive")
    extractions = FileSystemExtractionRepository(tmp_path / "derived")
    save_record(extractions, packet, 1, "hello world")
    report = OcrEvaluator(extractions=extractions, max_text_chars=3).evaluate(
        OcrEvaluationSet(samples=(sample(packet, 1, "hello world"),))
    )
    assert report.summary.oversized == 1
    assert report.cases[0].cer is None
    assert report.cases[0].error == "normalized text exceeds max_text_chars"


def test_synthetic_ocr_evaluation_example_matches_contract() -> None:
    path = Path(__file__).parents[2] / "examples" / "ocr-evaluation" / "samples.json"
    evaluation_set = OcrEvaluationSet.model_validate_json(path.read_bytes())
    assert evaluation_set.samples[0].sample_id == "synthetic-slide-1"
    assert evaluation_set.samples[0].request_id is None
