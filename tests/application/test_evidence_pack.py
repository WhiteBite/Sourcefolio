from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.extractions import FileSystemExtractionRepository
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.evidence_pack import (
    EvidencePackBuilder,
    EvidencePackDocument,
    evidence_pack_to_dict,
    render_evidence_pack_markdown,
)
from evidence_vault.application.evidence_pack_integrity import EvidencePackVerifier
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


def test_evidence_pack_links_ranked_sources_and_typed_derived_excerpts(
    tmp_path: Path,
) -> None:
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
                    external_id="source-1",
                    canonical_url="https://example.test/source-1",
                ),
                captured_at=NOW,
                collector=Provenance(
                    actor_kind=ActorKind.TOOL,
                    actor_id="collector-test",
                    method="test",
                    version="1",
                ),
                title="A source about retrieval",
                collections=(CollectionMembership(name="Ideas"),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption-needle"),
                    ),
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_IMAGE,
                        media_type="image/jpeg",
                        stream=BytesIO(b"first"),
                    ),
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_IMAGE,
                        media_type="image/jpeg",
                        stream=BytesIO(b"second"),
                    ),
                ),
            )
        )
        .packet
    )
    extractions = FileSystemExtractionRepository(tmp_path / "derived")
    for index, text in enumerate(("unrelated text", "derived-search-needle"), start=1):
        request = ExtractionRequest(
            source_id=packet.source_id,
            capture_id=packet.capture_id,
            artifact_id=packet.artifacts[index].artifact_id,
            kind=ExtractionKind.OCR_TEXT,
            locator=EvidenceLocator(kind=LocatorKind.SLIDE, slide=index),
            extractor=Provenance(
                actor_kind=ActorKind.TOOL,
                actor_id="tesseract",
                method="ocr",
                version="test",
            ),
            config={"language": "eng"},
        )
        extractions.save(
            ExtractionRecord.create(
                request=request,
                created_at=NOW,
                text=text,
                limitations=("synthetic OCR",),
            )
        )

    pack = EvidencePackBuilder(
        packets=packets,
        artifacts=artifacts,
        artifact_locator=artifacts,
        extractions=extractions,
        clock=lambda: NOW,
    ).build("derived-search-needle", excerpt_limit=1)

    assert len(pack.sources) == 1
    source = pack.sources[0]
    assert source.rank == 1
    assert source.excerpts[0].matched_query is True
    assert source.excerpts[0].record.request.locator.slide == 2
    payload = evidence_pack_to_dict(pack, artifact_locator=artifacts)
    assert payload["schema_version"] == "evidence-pack/v1"
    assert payload["pack_type"] == "retrieval_only"
    assert "answer" not in payload
    source_payload = payload["sources"][0]  # type: ignore[index]
    assert source_payload["artifacts"][0]["uri"].startswith("file:")  # type: ignore[index]
    excerpt = source_payload["excerpts"][0]  # type: ignore[index]
    assert excerpt["locator"]["canonical"] == "slide:2"  # type: ignore[index]
    assert excerpt["artifact_uri"].startswith("file:")  # type: ignore[index]
    assert excerpt["extractor"]["actor_id"] == "tesseract"  # type: ignore[index]
    assert excerpt["capture_id"] == packet.capture_id  # type: ignore[index]
    assert payload["filters"]["excerpt_limit"] == 1  # type: ignore[index]
    assert any("omitted" in item for item in payload["limitations"])  # type: ignore[union-attr]
    broken = deepcopy(payload)
    broken["sources"][0]["rank"] = 2  # type: ignore[index]
    with pytest.raises(ValidationError, match="ranks"):
        EvidencePackDocument.model_validate(broken)

    verifier = EvidencePackVerifier(
        packets=packets,
        artifacts=artifacts,
        extractions=extractions,
    )
    valid_document = EvidencePackDocument.model_validate(payload)
    valid_integrity = verifier.verify(valid_document, verify_bytes=True)
    assert valid_integrity.ok is True
    assert valid_integrity.bytes_checked == 3
    assert valid_integrity.bytes_valid == 3

    tampered = deepcopy(payload)
    tampered["sources"][0]["excerpts"][0]["excerpt"] = "tampered"  # type: ignore[index]
    tampered_integrity = verifier.verify(EvidencePackDocument.model_validate(tampered))
    assert not tampered_integrity.ok
    assert "excerpt_provenance_mismatch" in {issue.code for issue in tampered_integrity.issues}

    dangling = deepcopy(payload)
    dangling["sources"][0]["artifacts"][0]["artifact_id"] = "art_" + "0" * 64  # type: ignore[index]
    dangling["sources"][0]["artifacts"][0]["sha256"] = "0" * 64  # type: ignore[index]
    dangling["sources"][0]["excerpts"][0]["extraction_id"] = "ext_" + "0" * 64  # type: ignore[index]
    dangling_integrity = verifier.verify(EvidencePackDocument.model_validate(dangling))
    assert {"dangling_artifact_ref", "dangling_extraction_ref"} <= {
        issue.code for issue in dangling_integrity.issues
    }

    artifact = packet.artifacts[0]
    artifact_path = archive / "artifacts" / artifact.sha256[:2] / artifact.sha256
    artifact_path.write_bytes(b"corrupt")
    corrupted_integrity = verifier.verify(valid_document, verify_bytes=True)
    assert corrupted_integrity.bytes_checked == 3
    assert corrupted_integrity.bytes_valid == 2
    assert "artifact_hash_mismatch" in {issue.code for issue in corrupted_integrity.issues}
    packet_path = archive / "sources" / packet.source_id / f"{packet.capture_id}.json"
    packet_path.write_bytes(b"not valid packet json")
    invalid_packet_integrity = verifier.verify(valid_document)
    assert "invalid_source_packet" in {issue.code for issue in invalid_packet_integrity.issues}

    markdown = render_evidence_pack_markdown(pack, artifact_locator=artifacts)
    assert "retrieval only" in markdown
    assert "slide:2" in markdown
    assert excerpt["extraction_id"] in markdown  # type: ignore[index]
    assert "derived-search-needle" in markdown


def test_evidence_pack_marks_caption_hit_separately_from_derived_match(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    artifacts = FileSystemArtifactStore(archive)
    packets = FileSystemSourcePacketRepository(archive)
    packet = (
        ArchiveCapture(artifacts=artifacts, packets=packets)
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="test",
                    kind=SourceKind.TEXT,
                    external_id="text-1",
                ),
                captured_at=NOW,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption-needle"),
                    ),
                ),
            )
        )
        .packet
    )
    request = ExtractionRequest(
        source_id=packet.source_id,
        capture_id=packet.capture_id,
        artifact_id=packet.artifacts[0].artifact_id,
        kind=ExtractionKind.OCR_TEXT,
        locator=EvidenceLocator(kind=LocatorKind.TEXT_SPAN, start_char=0, end_char=7),
        extractor=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
    )
    extractions = FileSystemExtractionRepository(tmp_path / "derived")
    extractions.save(
        ExtractionRecord.create(request=request, created_at=NOW, text="other derived text")
    )
    pack = EvidencePackBuilder(
        packets=packets,
        artifacts=artifacts,
        artifact_locator=artifacts,
        extractions=extractions,
    ).build("caption-needle")
    assert pack.sources[0].excerpts[0].matched_query is False
    assert pack.sources[0].result.snippet
