from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sourcefolio.domain.common import ActorKind, Approval, Provenance
from sourcefolio.domain.evidence import EvidenceItem, EvidenceKind
from sourcefolio.domain.source import SourceRef

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SOURCE_REF = SourceRef(
    source_id="src_" + "1" * 64,
    capture_id="cap_" + "2" * 64,
    locator="timestamp:12.5-18.0",
    excerpt="exact excerpt",
)


def provenance(kind: ActorKind) -> Provenance:
    return Provenance(actor_kind=kind, actor_id=kind.value, method="test")


def test_creator_claim_requires_a_source() -> None:
    with pytest.raises(ValidationError, match="requires at least one source_ref"):
        EvidenceItem.create(
            kind=EvidenceKind.CREATOR_CLAIM,
            statement="The creator claims X",
            provenance=provenance(ActorKind.AGENT),
            created_at=NOW,
        )


def test_agent_inference_requires_agent_origin_and_basis() -> None:
    with pytest.raises(ValidationError, match="requires agent provenance"):
        EvidenceItem.create(
            kind=EvidenceKind.AGENT_INFERENCE,
            statement="This may be relevant",
            source_refs=(SOURCE_REF,),
            provenance=provenance(ActorKind.USER),
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="requires a source or evidence basis"):
        EvidenceItem.create(
            kind=EvidenceKind.AGENT_INFERENCE,
            statement="This may be relevant",
            provenance=provenance(ActorKind.AGENT),
            created_at=NOW,
        )


def test_user_outcome_can_be_a_direct_user_observation() -> None:
    item = EvidenceItem.create(
        kind=EvidenceKind.USER_OUTCOME,
        statement="I tried it and abandoned it after one hour",
        provenance=provenance(ActorKind.USER),
        created_at=NOW,
    )

    assert item.kind == EvidenceKind.USER_OUTCOME
    assert item.source_refs == ()


def test_agent_recorded_user_outcome_requires_approval() -> None:
    with pytest.raises(ValidationError, match="explicit user approval"):
        EvidenceItem.create(
            kind=EvidenceKind.USER_OUTCOME,
            statement="Imported outcome",
            provenance=provenance(ActorKind.AGENT),
            created_at=NOW,
        )

    item = EvidenceItem.create(
        kind=EvidenceKind.USER_OUTCOME,
        statement="Imported outcome",
        provenance=provenance(ActorKind.AGENT),
        approval=Approval(approved_by="user", approved_at=NOW),
        created_at=NOW,
    )
    assert item.approval is not None


def test_evidence_cannot_cite_itself() -> None:
    item = EvidenceItem.create(
        kind=EvidenceKind.AGENT_INFERENCE,
        statement="A tentative inference",
        source_refs=(SOURCE_REF,),
        provenance=provenance(ActorKind.AGENT),
        created_at=NOW,
    )
    changed = item.model_dump()
    changed["supporting_evidence_ids"] = (item.evidence_id,)

    with pytest.raises(ValidationError, match="cannot cite itself"):
        EvidenceItem(**changed)
