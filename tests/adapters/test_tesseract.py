from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from conftest import write_fake_executable
from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.extractions import FileSystemExtractionRepository
from evidence_vault.adapters.ocr.tesseract import TesseractConfig, TesseractOcrAdapter
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.extract_text import ExtractText
from evidence_vault.application.extraction_types import ExtractionError
from evidence_vault.application.search import SearchIndexBuilder
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.extraction import EvidenceLocator, ExtractionKind, LocatorKind
from evidence_vault.domain.source import ArtifactRole, SourceIdentity, SourceKind, SourcePacket

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def packet(root: Path) -> SourcePacket:
    use_case = ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    )
    return use_case.handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="test",
                kind=SourceKind.SLIDESHOW,
                external_id="slides",
            ),
            captured_at=NOW,
            collector=Provenance(
                actor_kind=ActorKind.TOOL,
                actor_id="test",
                method="test",
            ),
            title="Raw image only",
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.ORIGINAL_IMAGE,
                    media_type="image/jpeg",
                    stream=BytesIO(b"fake-image"),
                    original_filename="slide-01.jpg",
                ),
            ),
        )
    ).packet


def fake_tesseract(path: Path, counter: Path, behavior: str = "success") -> Path:
    body = {
        "success": 'print("Распознанный текст")',
        "large": 'print("x" * 100)',
        "timeout": "import time; time.sleep(30)",
    }[behavior]
    return write_fake_executable(
        path,
        f"""import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
if "--version" in sys.argv:
    print("fake-tesseract-1.0")
    raise SystemExit(0)
with Path({str(counter)!r}).open("a", encoding="utf-8") as counter:
    counter.write("run\\n")
{body}
""",
    )


def config(root: Path, executable: Path, **changes) -> TesseractConfig:
    base = TesseractConfig(
        executable=str(executable),
        temp_root=root / "tmp",
        min_free_bytes=0,
        timeout_seconds=2,
    )
    return replace(base, **changes)


def test_ocr_is_idempotent_versioned_and_searchable(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    source_packet = packet(archive)
    counter = tmp_path / "counter"
    executable = fake_tesseract(tmp_path / "fake-tesseract", counter)
    extractions = FileSystemExtractionRepository(derived)
    service = ExtractText(
        artifacts=FileSystemArtifactStore(archive),
        extractions=extractions,
        clock=lambda: NOW,
    )
    artifact = source_packet.artifacts[0]
    locator = EvidenceLocator(kind=LocatorKind.SLIDE, slide=1)
    adapter = TesseractOcrAdapter(config(tmp_path, executable))

    first = service.handle(
        packet=source_packet,
        artifact=artifact,
        locator=locator,
        adapter=adapter,
    )
    second = service.handle(
        packet=source_packet,
        artifact=artifact,
        locator=locator,
        adapter=adapter,
    )

    assert first.created is True
    assert second.created is False
    assert second.record.extraction_id == first.record.extraction_id
    assert first.record.request.kind == ExtractionKind.OCR_TEXT
    assert first.record.request.extractor.version == "fake-tesseract-1.0"
    assert first.record.excerpt == "Распознанный текст"
    assert counter.read_text(encoding="utf-8").splitlines() == ["run"]

    other_language = TesseractOcrAdapter(config(tmp_path, executable, language="eng"))
    third = service.handle(
        packet=source_packet,
        artifact=artifact,
        locator=locator,
        adapter=other_language,
    )
    assert third.created is True
    assert third.record.request_id != first.record.request_id
    assert len(tuple(extractions.iter_for_capture(source_packet.capture_id))) == 2

    index = SearchIndexBuilder(
        packets=FileSystemSourcePacketRepository(archive),
        artifacts=FileSystemArtifactStore(archive),
        extractions=extractions,
    ).build()
    assert [result.capture_id for result in index.search("распознанный")] == [
        source_packet.capture_id
    ]


def test_ocr_enforces_output_limit_and_timeout(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    source_packet = packet(archive)
    artifact = source_packet.artifacts[0]
    locator = EvidenceLocator(kind=LocatorKind.SLIDE, slide=1)
    counter = tmp_path / "counter"
    large = TesseractOcrAdapter(
        config(
            tmp_path,
            fake_tesseract(tmp_path / "large", counter, "large"),
            max_output_bytes=10,
        )
    )
    with pytest.raises(ExtractionError, match="output exceeds"):
        large.extract(artifact=artifact, content=BytesIO(b"image"), locator=locator)

    timeout = TesseractOcrAdapter(
        config(
            tmp_path,
            fake_tesseract(tmp_path / "timeout", counter, "timeout"),
            timeout_seconds=0.05,
        )
    )
    with pytest.raises(ExtractionError, match="timed out"):
        timeout.extract(artifact=artifact, content=BytesIO(b"image"), locator=locator)


def test_language_data_fingerprint_changes_the_request_config(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    executable = fake_tesseract(tmp_path / "fake-tesseract", counter)
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    model = tessdata / "eng.traineddata"
    model.write_bytes(b"model-one")

    first = TesseractOcrAdapter(config(tmp_path, executable, language="eng", tessdata_dir=tessdata))
    first.prepare()
    first_fingerprint = first.config["language_data_sha256"]

    model.write_bytes(b"model-two")
    second = TesseractOcrAdapter(
        config(tmp_path, executable, language="eng", tessdata_dir=tessdata)
    )
    second.prepare()

    assert first_fingerprint != "unverified"
    assert second.config["language_data_sha256"] != first_fingerprint

    missing_model = TesseractOcrAdapter(
        config(tmp_path, executable, language="rus", tessdata_dir=tessdata)
    )
    with pytest.raises(ExtractionError, match="language data not found"):
        missing_model.prepare()


def test_missing_ocr_executable_fails_safely(tmp_path: Path) -> None:
    adapter = TesseractOcrAdapter(
        TesseractConfig(
            executable=str(tmp_path / "missing"),
            temp_root=tmp_path / "tmp",
            min_free_bytes=0,
        )
    )

    with pytest.raises(ExtractionError, match="unable to start"):
        adapter.prepare()
