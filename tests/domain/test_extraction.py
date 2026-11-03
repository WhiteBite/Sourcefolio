from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.extraction import (
    EvidenceLocator,
    ExtractionKind,
    ExtractionRecord,
    ExtractionRequest,
    LocatorKind,
)


def request() -> ExtractionRequest:
    return ExtractionRequest(
        source_id="src_" + "1" * 64,
        capture_id="cap_" + "a" * 64,
        artifact_id="art_" + "b" * 64,
        kind=ExtractionKind.OCR_TEXT,
        locator=EvidenceLocator(kind=LocatorKind.SLIDE, slide=1),
        extractor=Provenance(
            actor_kind=ActorKind.TOOL,
            actor_id="tesseract",
            method="subprocess-ocr",
            version="1",
        ),
        config={"language": "eng+rus"},
    )


def test_locators_are_typed_and_canonical() -> None:
    slide = EvidenceLocator(kind=LocatorKind.SLIDE, slide=2)
    timestamp = EvidenceLocator(
        kind=LocatorKind.TIMESTAMP,
        start_seconds=1.25,
        end_seconds=3.5,
    )
    span = EvidenceLocator(kind=LocatorKind.TEXT_SPAN, start_char=4, end_char=9)

    assert slide.canonical == "slide:2"
    assert timestamp.canonical == "timestamp:1.250-3.500"
    assert span.canonical == "text_span:4-9"
    with pytest.raises(ValidationError):
        EvidenceLocator(kind=LocatorKind.TIMESTAMP, start_seconds=2, end_seconds=2)
    with pytest.raises(ValidationError):
        EvidenceLocator(kind=LocatorKind.SLIDE, slide=1, start_char=0)


def test_extraction_record_has_stable_request_and_content_ids() -> None:
    extraction_request = request()
    record = ExtractionRecord.create(
        request=extraction_request,
        created_at=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
        text="  Распознанный\n текст  ",
    )

    assert record.request_id == extraction_request.request_id
    assert record.excerpt == "Распознанный текст"
    assert ExtractionRecord.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError, match="extraction_id"):
        ExtractionRecord.model_validate(record.model_dump() | {"extraction_id": "ext_" + "0" * 64})
