from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from sourcefolio.domain.common import (
    Approval,
    ContractModel,
    Provenance,
    UtcDateTime,
    content_id,
    require_human_ownership,
)
from sourcefolio.domain.extraction import ExtractionId
from sourcefolio.domain.source import ArtifactId, CaptureId, SourceId

CandidateId = Annotated[str, StringConstraints(pattern=r"^cand_[0-9a-f]{64}$")]
MentionId = Annotated[str, StringConstraints(pattern=r"^men_[0-9a-f]{64}$")]
RouteId = Annotated[str, StringConstraints(pattern=r"^rt_[0-9a-f]{64}$")]
SnapshotId = Annotated[str, StringConstraints(pattern=r"^snap_[0-9a-f]{64}$")]
ProjectId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
CandidateKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9:._/-]*$", min_length=1, max_length=200),
]


class CandidateKind(StrEnum):
    TOOL = "tool"
    LIBRARY = "library"
    SERVICE = "service"
    TECHNIQUE = "technique"
    PATTERN = "pattern"
    MODEL = "model"
    DATASET = "dataset"
    REPOSITORY = "repository"
    TOPIC = "topic"
    OTHER = "other"


class RouteVerdict(StrEnum):
    CANDIDATE = "candidate"
    IRRELEVANT = "irrelevant"
    INSUFFICIENT_SIGNAL = "insufficient_signal"


class AnchorField(StrEnum):
    DESCRIPTION = "description"
    COMMENT = "comment"
    OCR = "ocr"
    CAPTION = "caption"
    TITLE = "title"


class FitLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DecisionStatus(StrEnum):
    INBOX = "inbox"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class MentionAnchor(ContractModel):
    field: AnchorField
    quote: str = Field(min_length=8, max_length=500)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    comment_index: int | None = Field(default=None, ge=0)
    extraction_id: ExtractionId | None = None
    artifact_id: ArtifactId | None = None

    @model_validator(mode="after")
    def locator_matches_field(self) -> MentionAnchor:
        if self.end_char - self.start_char != len(self.quote):
            raise ValueError("quote length must match character offsets")
        if self.field == AnchorField.COMMENT:
            if self.comment_index is None:
                raise ValueError("comment anchor requires comment_index")
            if self.extraction_id is not None or self.artifact_id is not None:
                raise ValueError("comment anchor accepts only comment_index")
        elif self.field == AnchorField.OCR:
            if self.extraction_id is None:
                raise ValueError("ocr anchor requires extraction_id")
            if self.comment_index is not None or self.artifact_id is not None:
                raise ValueError("ocr anchor accepts only extraction_id")
        elif self.field == AnchorField.CAPTION:
            if self.artifact_id is None:
                raise ValueError("caption anchor requires artifact_id")
            if self.comment_index is not None or self.extraction_id is not None:
                raise ValueError("caption anchor accepts only artifact_id")
        elif (
            self.comment_index is not None
            or self.extraction_id is not None
            or self.artifact_id is not None
        ):
            raise ValueError("description or title anchor accepts no locator fields")
        return self


class CandidateMention(ContractModel):
    mention_id: MentionId
    candidate_key: CandidateKey
    kind: CandidateKind
    title: str = Field(min_length=1)
    fit: FitLevel
    anchor: MentionAnchor

    @model_validator(mode="after")
    def id_matches_content(self) -> CandidateMention:
        payload = self.model_dump(mode="json", exclude={"mention_id"})
        if self.mention_id != content_id("men", payload):
            raise ValueError("mention_id does not match mention content")
        return self


class RegistryRoute(ContractModel):
    schema_version: Literal["registry-route/v1"] = "registry-route/v1"
    route_id: RouteId
    source_id: SourceId
    capture_id: CaptureId
    source_url: str = ""
    source_title: str = ""
    project_id: ProjectId
    model: str = Field(min_length=1)
    prompt_fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")
    verdict: RouteVerdict
    reason: str = ""
    mentions: tuple[CandidateMention, ...] = ()
    fit: FitLevel = FitLevel.LOW
    pre_rank: int = Field(default=0, ge=0)
    captured_at: UtcDateTime
    provenance: Provenance
    created_at: UtcDateTime
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def identity_and_verdict_are_valid(self) -> RegistryRoute:
        payload = {
            "source_id": self.source_id,
            "capture_id": self.capture_id,
            "project_id": self.project_id,
            "model": self.model,
            "prompt_fingerprint": self.prompt_fingerprint,
        }
        if self.route_id != content_id("rt", payload):
            raise ValueError("route_id does not match route identity")
        if self.verdict == RouteVerdict.CANDIDATE:
            if not self.mentions:
                raise ValueError("candidate verdict requires at least one mention")
        elif self.mentions:
            raise ValueError("non-candidate verdict requires zero mentions")
        if self.verdict == RouteVerdict.INSUFFICIENT_SIGNAL and not self.reason:
            raise ValueError("insufficient_signal requires a reason")
        if any(not limitation for limitation in self.limitations):
            raise ValueError("limitations must be non-empty")
        return self


class Candidate(ContractModel):
    schema_version: Literal["candidate-record/v1"] = "candidate-record/v1"
    candidate_id: CandidateId
    project_id: ProjectId
    key: CandidateKey
    kind: CandidateKind
    title: str = Field(min_length=1)
    fit: FitLevel
    aliases: tuple[CandidateKey, ...] = ()
    merged_from: tuple[CandidateId, ...] = ()
    mention_ids: tuple[MentionId, ...] = ()
    source_ids: tuple[SourceId, ...] = ()
    mention_count: int = Field(default=0, ge=0)
    first_capture_at: UtcDateTime | None = None
    last_capture_at: UtcDateTime | None = None
    stale: bool = False

    @model_validator(mode="after")
    def identity_and_tuples_are_valid(self) -> Candidate:
        payload = {"project_id": self.project_id, "key": self.key}
        if self.candidate_id != content_id("cand", payload):
            raise ValueError("candidate_id does not match candidate identity")
        for values in (self.aliases, self.merged_from, self.mention_ids, self.source_ids):
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise ValueError("candidate tuples must be sorted and unique")
        if not self.stale and self.mention_count != len(self.mention_ids):
            raise ValueError("mention_count must match mention_ids for a non-stale candidate")
        return self


class SnapshotProject(ContractModel):
    project_id: ProjectId
    name: str = Field(min_length=1)
    candidate_count: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    stale: int = Field(ge=0)


class RegistrySnapshot(ContractModel):
    schema_version: Literal["registry-snapshot/v1"] = "registry-snapshot/v1"
    snapshot_id: SnapshotId
    generated_at: UtcDateTime
    projects: tuple[SnapshotProject, ...]
    candidates: tuple[Candidate, ...]
    route_count: int = Field(ge=0)
    insufficient_signal_routes: int = Field(ge=0)
    dropped_claims: int = Field(ge=0)

    @model_validator(mode="after")
    def identity_and_ordering_are_valid(self) -> RegistrySnapshot:
        payload = self.model_dump(mode="json", exclude={"snapshot_id", "generated_at"})
        if self.snapshot_id != content_id("snap", payload):
            raise ValueError("snapshot_id does not match snapshot content")
        if tuple(sorted(project.project_id for project in self.projects)) != tuple(
            project.project_id for project in self.projects
        ):
            raise ValueError("snapshot projects are sorted by project_id")
        if tuple(sorted(value.candidate_id for value in self.candidates)) != tuple(
            value.candidate_id for value in self.candidates
        ):
            raise ValueError("snapshot candidates are sorted by candidate_id")
        return self


class CandidateDecision(ContractModel):
    schema_version: Literal["candidate-decision/v1"] = "candidate-decision/v1"
    candidate_id: CandidateId
    project_id: ProjectId
    key: CandidateKey
    kind: CandidateKind
    title: str = Field(min_length=1)
    status: DecisionStatus = DecisionStatus.INBOX
    rationale: str = ""
    decided_at: UtcDateTime | None = None
    provenance: Provenance
    approval: Approval | None = None

    @model_validator(mode="after")
    def decision_ownership_is_explicit(self) -> CandidateDecision:
        if self.status == DecisionStatus.INBOX:
            if self.decided_at is not None or self.approval is not None:
                raise ValueError("inbox decision requires no decided_at or approval")
            return self
        if self.decided_at is None:
            raise ValueError("decided candidate requires decided_at")
        require_human_ownership(
            self.provenance,
            self.approval,
            record_name="candidate decision",
        )
        return self
