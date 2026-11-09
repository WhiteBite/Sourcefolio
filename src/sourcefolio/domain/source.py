from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from sourcefolio.domain.common import (
    ContractModel,
    NonEmptyStr,
    Provenance,
    UtcDateTime,
    content_id,
)

SourceId = Annotated[str, StringConstraints(pattern=r"^src_[0-9a-f]{64}$")]
CaptureId = Annotated[str, StringConstraints(pattern=r"^cap_[0-9a-f]{64}$")]
ArtifactId = Annotated[str, StringConstraints(pattern=r"^art_[0-9a-f]{64}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PlatformName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    ),
]


class SourceKind(StrEnum):
    VIDEO = "video"
    SLIDESHOW = "slideshow"
    WEB_PAGE = "web_page"
    REPOSITORY = "repository"
    DOCUMENT = "document"
    TEXT = "text"
    OTHER = "other"


class ArtifactRole(StrEnum):
    """Raw capture roles only; OCR, ASR, and embeddings are derived records."""

    ORIGINAL_VIDEO = "original_video"
    ORIGINAL_AUDIO = "original_audio"
    ORIGINAL_IMAGE = "original_image"
    CAPTION_TEXT = "caption_text"
    PAGE_SNAPSHOT = "page_snapshot"
    AUTHOR_METADATA = "author_metadata"
    ATTACHMENT = "attachment"
    OTHER = "other"


class CaptureStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class SourceIdentity(ContractModel):
    platform: PlatformName
    kind: SourceKind
    external_id: NonEmptyStr | None = None
    canonical_url: NonEmptyStr | None = None

    @field_validator("platform", mode="before")
    @classmethod
    def normalize_platform(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def has_stable_locator(self) -> SourceIdentity:
        if self.external_id is None and self.canonical_url is None:
            raise ValueError("source requires external_id or canonical_url")
        return self

    @property
    def identity_key(self) -> dict[str, str]:
        if self.external_id is not None:
            return {"platform": self.platform, "external_id": self.external_id}
        return {"platform": self.platform, "canonical_url": self.canonical_url or ""}


def source_id_for(source: SourceIdentity) -> str:
    return content_id("src", source.identity_key)


class CollectionMembership(ContractModel):
    name: NonEmptyStr
    external_id: NonEmptyStr | None = None


class ArtifactRef(ContractModel):
    artifact_id: ArtifactId
    role: ArtifactRole
    media_type: NonEmptyStr
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    original_filename: NonEmptyStr | None = None

    @model_validator(mode="after")
    def id_matches_digest(self) -> ArtifactRef:
        if self.artifact_id != f"art_{self.sha256}":
            raise ValueError("artifact_id must equal art_<sha256>")
        return self


class SourceRef(ContractModel):
    source_id: SourceId
    capture_id: CaptureId
    artifact_id: ArtifactId | None = None
    locator: NonEmptyStr | None = None
    excerpt: NonEmptyStr | None = None


class CaptureContent(ContractModel):
    schema_version: Literal["source-packet/v1"] = "source-packet/v1"
    source: SourceIdentity
    captured_at: UtcDateTime
    collector: Provenance
    status: CaptureStatus = CaptureStatus.COMPLETE
    title: NonEmptyStr | None = None
    collections: tuple[CollectionMembership, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = Field(min_length=1)
    limitations: tuple[NonEmptyStr, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def completeness_is_explicit(self) -> CaptureContent:
        if self.status == CaptureStatus.PARTIAL and not self.limitations:
            raise ValueError("partial capture requires at least one limitation")
        if self.status == CaptureStatus.COMPLETE and self.limitations:
            raise ValueError("complete capture cannot contain limitations")
        return self


class SourcePacket(CaptureContent):
    source_id: SourceId
    capture_id: CaptureId

    @model_validator(mode="after")
    def ids_match_content(self) -> SourcePacket:
        expected_source_id = source_id_for(self.source)
        if self.source_id != expected_source_id:
            raise ValueError("source_id does not match source identity")
        fingerprint = self.model_dump(
            mode="json",
            exclude={"source_id", "capture_id"},
        )
        expected_capture_id = content_id("cap", fingerprint)
        if self.capture_id != expected_capture_id:
            raise ValueError("capture_id does not match capture content")
        return self

    @classmethod
    def create(
        cls,
        *,
        source: SourceIdentity,
        captured_at: UtcDateTime,
        collector: Provenance,
        artifacts: tuple[ArtifactRef, ...],
        status: CaptureStatus = CaptureStatus.COMPLETE,
        title: NonEmptyStr | None = None,
        collections: tuple[CollectionMembership, ...] = (),
        limitations: tuple[NonEmptyStr, ...] = (),
        metadata: dict[str, JsonValue] | None = None,
    ) -> SourcePacket:
        content = CaptureContent(
            source=source,
            captured_at=captured_at,
            collector=collector,
            status=status,
            title=title,
            collections=collections,
            artifacts=artifacts,
            limitations=limitations,
            metadata=metadata or {},
        )
        capture_id = content_id("cap", content.model_dump(mode="json"))
        return cls(
            **content.model_dump(),
            source_id=source_id_for(source),
            capture_id=capture_id,
        )
