from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import yaml

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.extractions import FileSystemExtractionRepository
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.obsidian_views import ObsidianOverviewProjection
from evidence_vault.application.source_cards import SourceCardProjection
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.extraction import (
    EvidenceLocator,
    ExtractionKind,
    ExtractionRecord,
    ExtractionRequest,
    LocatorKind,
)
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def test_obsidian_views_include_derived_text_and_coverage(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    artifacts = FileSystemArtifactStore(archive)
    packets = FileSystemSourcePacketRepository(archive)
    packet = (
        ArchiveCapture(artifacts=artifacts, packets=packets)
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="test",
                    kind=SourceKind.SLIDESHOW,
                    external_id="slides-1",
                ),
                captured_at=NOW,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                title="OCR source",
                collections=(CollectionMembership(name="Идеи"),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_IMAGE,
                        media_type="image/jpeg",
                        stream=BytesIO(b"image"),
                    ),
                ),
            )
        )
        .packet
    )
    extractions = FileSystemExtractionRepository(tmp_path / "derived")
    request = ExtractionRequest(
        source_id=packet.source_id,
        capture_id=packet.capture_id,
        artifact_id=packet.artifacts[0].artifact_id,
        kind=ExtractionKind.OCR_TEXT,
        locator=EvidenceLocator(kind=LocatorKind.SLIDE, slide=1),
        extractor=Provenance(
            actor_kind=ActorKind.TOOL,
            actor_id="tesseract",
            method="ocr",
            version="test",
        ),
    )
    record = ExtractionRecord.create(
        request=request,
        created_at=NOW,
        text="derived Obsidian needle",
        limitations=("synthetic OCR",),
    )
    extractions.save(record)
    generated = tmp_path / "vault" / "_generated" / "evidence-vault"
    store = FileSystemProjectionStore(generated)

    card = SourceCardProjection(
        packets=packets,
        artifacts=artifacts,
        store=store,
        extractions=extractions,
    ).render(packet)
    properties = yaml.safe_load(card.content.split("---", 2)[1])
    assert properties["derived_extraction_count"] == 1
    assert properties["has_derived_text"] is True
    assert record.extraction_id in card.content
    assert "slide:1" in card.content
    assert "derived Obsidian needle" in card.content
    assert "synthetic OCR" in card.content
    assert "file:" in card.content

    assert (
        ObsidianOverviewProjection(
            packets=packets,
            store=store,
            extractions=extractions,
            clock=lambda: NOW,
        ).rebuild()
        == 1
    )
    overview = (generated / "overview" / "index.md").read_text(encoding="utf-8")
    assert "capture_count: 1" in overview
    assert "derived_extraction_count: 1" in overview
    assert "| 1 | 1 | 1 | 1 |" in overview
    assert "ocr\\_text" in overview
    assert "Идеи" in overview
    assert f"../source-cards/{packet.source_id}/{packet.capture_id}.md" in overview
