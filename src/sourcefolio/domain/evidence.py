from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

from sourcefolio.domain.common import (
    ActorKind,
    Approval,
    ContractModel,
    NonEmptyStr,
    Provenance,
    UtcDateTime,
    content_id,
    require_human_ownership,
)
from sourcefolio.domain.source import SourceRef

EvidenceId = Annotated[str, StringConstraints(pattern=r"^ev_[0-9a-f]{64}$")]


class EvidenceKind(StrEnum):
    CREATOR_CLAIM = "creator_claim"
    AGENT_INFERENCE = "agent_inference"
    EXTERNAL_OBSERVATION = "external_observation"
    USER_OUTCOME = "user_outcome"


class EvidenceContent(ContractModel):
    schema_version: Literal["evidence-item/v1"] = "evidence-item/v1"
    kind: EvidenceKind
    statement: NonEmptyStr
    source_refs: tuple[SourceRef, ...] = ()
    supporting_evidence_ids: tuple[EvidenceId, ...] = ()
    provenance: Provenance
    approval: Approval | None = None
    created_at: UtcDateTime

    @model_validator(mode="after")
    def basis_and_origin_are_explicit(self) -> EvidenceContent:
        if (
            self.kind
            in {
                EvidenceKind.CREATOR_CLAIM,
                EvidenceKind.EXTERNAL_OBSERVATION,
            }
            and not self.source_refs
        ):
            raise ValueError(f"{self.kind} requires at least one source_ref")

        if self.kind == EvidenceKind.AGENT_INFERENCE:
            if self.provenance.actor_kind != ActorKind.AGENT:
                raise ValueError("agent_inference requires agent provenance")
            if not self.source_refs and not self.supporting_evidence_ids:
                raise ValueError("agent_inference requires a source or evidence basis")

        if self.kind == EvidenceKind.USER_OUTCOME:
            require_human_ownership(
                self.provenance,
                self.approval,
                record_name="user_outcome",
            )
        elif self.approval is not None:
            raise ValueError("approval is only valid for user_outcome")

        return self


class EvidenceItem(EvidenceContent):
    evidence_id: EvidenceId

    @model_validator(mode="after")
    def id_and_references_are_valid(self) -> EvidenceItem:
        if self.evidence_id in self.supporting_evidence_ids:
            raise ValueError("evidence cannot cite itself")
        fingerprint = self.model_dump(mode="json", exclude={"evidence_id"})
        if self.evidence_id != content_id("ev", fingerprint):
            raise ValueError("evidence_id does not match evidence content")
        return self

    @classmethod
    def create(
        cls,
        *,
        kind: EvidenceKind,
        statement: NonEmptyStr,
        provenance: Provenance,
        created_at: UtcDateTime,
        source_refs: tuple[SourceRef, ...] = (),
        supporting_evidence_ids: tuple[EvidenceId, ...] = (),
        approval: Approval | None = None,
    ) -> EvidenceItem:
        content = EvidenceContent(
            kind=kind,
            statement=statement,
            source_refs=source_refs,
            supporting_evidence_ids=supporting_evidence_ids,
            provenance=provenance,
            approval=approval,
            created_at=created_at,
        )
        evidence_id = content_id("ev", content.model_dump(mode="json"))
        return cls(
            **content.model_dump(),
            evidence_id=evidence_id,
        )
