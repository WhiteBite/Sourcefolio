from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from evidence_vault.domain.common import (
    ContractModel,
    NonEmptyStr,
    Provenance,
    UtcDateTime,
    content_id,
)
from evidence_vault.domain.source import ArtifactId, CaptureId, SourceId

ExtractionId = Annotated[str, StringConstraints(pattern=r"^ext_[0-9a-f]{64}$")]
ExtractionRequestId = Annotated[str, StringConstraints(pattern=r"^exq_[0-9a-f]{64}$")]


class LocatorKind(StrEnum):
    SLIDE = "slide"
    TIMESTAMP = "timestamp"
    TEXT_SPAN = "text_span"


class EvidenceLocator(ContractModel):
    kind: LocatorKind
    slide: int | None = Field(default=None, ge=1)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def fields_match_kind(self) -> EvidenceLocator:
        if self.kind == LocatorKind.SLIDE:
            if self.slide is None or any(
                value is not None
                for value in (
                    self.start_seconds,
                    self.end_seconds,
                    self.start_char,
                    self.end_char,
                )
            ):
                raise ValueError("slide locator requires only slide")
        elif self.kind == LocatorKind.TIMESTAMP:
            if (
                self.start_seconds is None
                or self.end_seconds is None
                or self.end_seconds <= self.start_seconds
                or any(value is not None for value in (self.slide, self.start_char, self.end_char))
            ):
                raise ValueError("timestamp locator requires an ordered half-open interval")
        elif (
            self.start_char is None
            or self.end_char is None
            or self.end_char <= self.start_char
            or any(
                value is not None for value in (self.slide, self.start_seconds, self.end_seconds)
            )
        ):
            raise ValueError("text-span locator requires ordered character offsets")
        return self

    @property
    def canonical(self) -> str:
        if self.kind == LocatorKind.SLIDE:
            return f"slide:{self.slide}"
        if self.kind == LocatorKind.TIMESTAMP:
            return f"timestamp:{self.start_seconds:.3f}-{self.end_seconds:.3f}"
        return f"text_span:{self.start_char}-{self.end_char}"


class ExtractionKind(StrEnum):
    OCR_TEXT = "ocr_text"
    ASR_TEXT = "asr_text"


class ExtractionRequest(ContractModel):
    source_id: SourceId
    capture_id: CaptureId
    artifact_id: ArtifactId
    kind: ExtractionKind
    locator: EvidenceLocator
    extractor: Provenance
    config: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def request_id(self) -> str:
        return content_id(
            "exq",
            {
                "schema_version": "extraction-request/v1",
                **self.model_dump(mode="json"),
            },
        )


def _excerpt(text: str, limit: int = 500) -> str:
    return " ".join(text.split())[:limit]


class ExtractionContent(ContractModel):
    schema_version: Literal["extraction-record/v1"] = "extraction-record/v1"
    request: ExtractionRequest
    request_id: ExtractionRequestId
    created_at: UtcDateTime
    text: str
    excerpt: str
    limitations: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def request_and_excerpt_match(self) -> ExtractionContent:
        if self.request_id != self.request.request_id:
            raise ValueError("request_id does not match extraction request")
        if self.excerpt != _excerpt(self.text):
            raise ValueError("excerpt does not match extracted text")
        return self


class ExtractionRecord(ExtractionContent):
    """Derived text, not a creator claim and not part of the immutable raw packet."""

    extraction_id: ExtractionId

    @model_validator(mode="after")
    def id_matches_content(self) -> ExtractionRecord:
        expected = content_id(
            "ext",
            self.model_dump(mode="json", exclude={"extraction_id"}),
        )
        if self.extraction_id != expected:
            raise ValueError("extraction_id does not match extraction content")
        return self

    @classmethod
    def create(
        cls,
        *,
        request: ExtractionRequest,
        created_at: UtcDateTime,
        text: str,
        limitations: tuple[NonEmptyStr, ...] = (),
    ) -> ExtractionRecord:
        content = ExtractionContent(
            request=request,
            request_id=request.request_id,
            created_at=created_at,
            text=text,
            excerpt=_excerpt(text),
            limitations=limitations,
        )
        extraction_id = content_id("ext", content.model_dump(mode="json"))
        return cls(**content.model_dump(), extraction_id=extraction_id)
