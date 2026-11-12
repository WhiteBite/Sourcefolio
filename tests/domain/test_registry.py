from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sourcefolio.domain.common import ActorKind, Approval, Provenance, content_id
from sourcefolio.domain.registry import (
    AnchorField,
    Candidate,
    CandidateDecision,
    CandidateKind,
    CandidateMention,
    DecisionStatus,
    FitLevel,
    MentionAnchor,
    RegistryRoute,
    RegistrySnapshot,
    RouteVerdict,
    SnapshotProject,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SOURCE_ID = "src_" + "1" * 64
CAPTURE_ID = "cap_" + "2" * 64
ARTIFACT_ID = "art_" + "3" * 64
EXTRACTION_ID = "ext_" + "4" * 64
PROJECT_ID = "sourcefolio"
CANDIDATE_KEY = "owner/repository"
PROMPT_FINGERPRINT = "0123456789abcdef"


def provenance(kind: ActorKind = ActorKind.AGENT) -> Provenance:
    return Provenance(actor_kind=kind, actor_id=kind.value, method="registry-test")


def mention_payload() -> dict[str, object]:
    return {
        "candidate_key": CANDIDATE_KEY,
        "kind": CandidateKind.TOOL,
        "title": "Example tool",
        "fit": FitLevel.HIGH,
        "anchor": MentionAnchor(
            field=AnchorField.DESCRIPTION,
            quote="useful example tool",
            start_char=0,
            end_char=19,
        ).model_dump(mode="json"),
    }


def mention() -> CandidateMention:
    payload = mention_payload()
    return CandidateMention(
        mention_id=content_id("men", payload),
        **payload,
    )


def route_payload() -> dict[str, str]:
    return {
        "source_id": SOURCE_ID,
        "capture_id": CAPTURE_ID,
        "project_id": PROJECT_ID,
        "model": "test-model",
        "prompt_fingerprint": PROMPT_FINGERPRINT,
    }


def route(**overrides: object) -> RegistryRoute:
    values: dict[str, object] = {
        "route_id": content_id("rt", route_payload()),
        "source_id": SOURCE_ID,
        "capture_id": CAPTURE_ID,
        "project_id": PROJECT_ID,
        "model": "test-model",
        "prompt_fingerprint": PROMPT_FINGERPRINT,
        "verdict": RouteVerdict.CANDIDATE,
        "mentions": (mention(),),
        "captured_at": NOW,
        "provenance": provenance(),
        "created_at": NOW,
    }
    values.update(overrides)
    return RegistryRoute(**values)


def candidate_payload() -> dict[str, str]:
    return {"project_id": PROJECT_ID, "key": CANDIDATE_KEY}


def candidate(**overrides: object) -> Candidate:
    values: dict[str, object] = {
        "candidate_id": content_id("cand", candidate_payload()),
        "project_id": PROJECT_ID,
        "key": CANDIDATE_KEY,
        "kind": CandidateKind.TOOL,
        "title": "Example tool",
        "fit": FitLevel.HIGH,
        "mention_ids": (mention().mention_id,),
        "source_ids": (SOURCE_ID,),
        "mention_count": 1,
    }
    values.update(overrides)
    return Candidate(**values)


def snapshot_payload(
    projects: tuple[SnapshotProject, ...],
    candidates: tuple[Candidate, ...],
) -> dict[str, object]:
    return {
        "schema_version": "registry-snapshot/v1",
        "projects": [project.model_dump(mode="json") for project in projects],
        "candidates": [value.model_dump(mode="json") for value in candidates],
        "route_count": 1,
        "insufficient_signal_routes": 0,
        "dropped_claims": 0,
    }


def snapshot(**overrides: object) -> RegistrySnapshot:
    projects = (
        SnapshotProject(
            project_id=PROJECT_ID,
            name="Sourcefolio",
            candidate_count=1,
            accepted=0,
            rejected=0,
            stale=0,
        ),
    )
    candidates = (candidate(),)
    values: dict[str, object] = {
        "snapshot_id": content_id(
            "snap",
            snapshot_payload(projects, candidates),
        ),
        "generated_at": NOW,
        "projects": projects,
        "candidates": candidates,
        "route_count": 1,
        "insufficient_signal_routes": 0,
        "dropped_claims": 0,
    }
    values.update(overrides)
    return RegistrySnapshot(**values)


def decision(**overrides: object) -> CandidateDecision:
    values: dict[str, object] = {
        "candidate_id": candidate().candidate_id,
        "project_id": PROJECT_ID,
        "key": CANDIDATE_KEY,
        "kind": CandidateKind.TOOL,
        "title": "Example tool",
        "provenance": provenance(),
    }
    values.update(overrides)
    return CandidateDecision(**values)


def test_mention_anchor_happy_path() -> None:
    anchor = MentionAnchor(
        field=AnchorField.COMMENT,
        quote="useful example tool",
        start_char=4,
        end_char=23,
        comment_index=2,
    )

    assert anchor.field == AnchorField.COMMENT
    assert anchor.comment_index == 2


def test_mention_anchor_requires_quote_length_to_match_offsets() -> None:
    with pytest.raises(ValidationError, match="quote length"):
        MentionAnchor(
            field=AnchorField.DESCRIPTION,
            quote="useful example tool",
            start_char=0,
            end_char=17,
        )


def test_comment_anchor_requires_comment_index_only() -> None:
    with pytest.raises(ValidationError, match="comment anchor"):
        MentionAnchor(
            field=AnchorField.COMMENT,
            quote="useful example tool",
            start_char=0,
            end_char=19,
        )


def test_ocr_anchor_requires_extraction_id_only() -> None:
    with pytest.raises(ValidationError, match="ocr anchor"):
        MentionAnchor(
            field=AnchorField.OCR,
            quote="useful example tool",
            start_char=0,
            end_char=19,
        )


def test_caption_anchor_requires_artifact_id_only() -> None:
    with pytest.raises(ValidationError, match="caption anchor"):
        MentionAnchor(
            field=AnchorField.CAPTION,
            quote="useful example tool",
            start_char=0,
            end_char=19,
        )


def test_description_and_title_anchors_reject_locator_fields() -> None:
    with pytest.raises(ValidationError, match="description or title anchor"):
        MentionAnchor(
            field=AnchorField.DESCRIPTION,
            quote="useful example tool",
            start_char=0,
            end_char=19,
            comment_index=0,
        )


def test_candidate_mention_happy_path() -> None:
    value = mention()

    assert value.candidate_key == CANDIDATE_KEY
    assert value.mention_id.startswith("men_")


def test_candidate_mention_id_matches_content() -> None:
    payload = mention_payload()

    with pytest.raises(ValidationError, match="mention_id does not match"):
        CandidateMention(mention_id="men_" + "0" * 64, **payload)


def test_registry_route_happy_path() -> None:
    value = route()

    assert value.verdict == RouteVerdict.CANDIDATE
    assert len(value.mentions) == 1


def test_registry_route_id_matches_identity_payload() -> None:
    with pytest.raises(ValidationError, match="route_id does not match"):
        route(route_id="rt_" + "0" * 64)


def test_candidate_verdict_requires_mentions() -> None:
    with pytest.raises(ValidationError, match="candidate verdict requires"):
        route(mentions=())


def test_irrelevant_verdict_rejects_mentions() -> None:
    with pytest.raises(ValidationError, match="non-candidate verdict"):
        route(verdict=RouteVerdict.IRRELEVANT)


def test_insufficient_signal_verdict_rejects_mentions() -> None:
    with pytest.raises(ValidationError, match="non-candidate verdict"):
        route(verdict=RouteVerdict.INSUFFICIENT_SIGNAL, reason="no signal")


def test_insufficient_signal_verdict_requires_reason() -> None:
    with pytest.raises(ValidationError, match="insufficient_signal requires a reason"):
        route(verdict=RouteVerdict.INSUFFICIENT_SIGNAL, mentions=())


def test_registry_route_rejects_empty_limitation() -> None:
    with pytest.raises(ValidationError):
        route(limitations=("",))


def test_candidate_happy_path() -> None:
    value = candidate()

    assert value.key == CANDIDATE_KEY
    assert value.mention_count == 1


def test_candidate_id_matches_project_and_key() -> None:
    with pytest.raises(ValidationError, match="candidate_id does not match"):
        candidate(candidate_id="cand_" + "0" * 64)


def test_candidate_tuples_are_sorted_and_unique() -> None:
    with pytest.raises(ValidationError, match="sorted and unique"):
        candidate(
            aliases=(CANDIDATE_KEY, "alias"),
            mention_ids=(mention().mention_id, mention().mention_id),
            source_ids=(SOURCE_ID, SOURCE_ID),
            merged_from=("cand_" + "5" * 64, "cand_" + "4" * 64),
        )


def test_non_stale_candidate_mention_count_matches_mentions() -> None:
    with pytest.raises(ValidationError, match="mention_count"):
        candidate(mention_count=2)


def test_snapshot_project_happy_path() -> None:
    value = SnapshotProject(
        project_id=PROJECT_ID,
        name="Sourcefolio",
        candidate_count=3,
        accepted=1,
        rejected=1,
        stale=1,
    )

    assert value.candidate_count == 3


def test_registry_snapshot_happy_path() -> None:
    value = snapshot()

    assert value.route_count == 1
    assert value.snapshot_id.startswith("snap_")


def test_registry_snapshot_id_matches_content() -> None:
    with pytest.raises(ValidationError, match="snapshot_id does not match"):
        snapshot(snapshot_id="snap_" + "0" * 64)


def test_registry_snapshot_projects_are_sorted() -> None:
    projects = (
        SnapshotProject(
            project_id="zeta",
            name="Zeta",
            candidate_count=0,
            accepted=0,
            rejected=0,
            stale=0,
        ),
        SnapshotProject(
            project_id="alpha",
            name="Alpha",
            candidate_count=0,
            accepted=0,
            rejected=0,
            stale=0,
        ),
    )
    candidates = (candidate(),)

    with pytest.raises(ValidationError, match="projects are sorted"):
        snapshot(
            snapshot_id=content_id("snap", snapshot_payload(projects, candidates)),
            projects=projects,
            candidates=candidates,
        )


def test_registry_snapshot_candidates_are_sorted() -> None:
    projects = (
        SnapshotProject(
            project_id=PROJECT_ID,
            name="Sourcefolio",
            candidate_count=1,
            accepted=0,
            rejected=0,
            stale=0,
        ),
    )
    candidates = (
        candidate(
            key="zeta",
            candidate_id=content_id("cand", {"project_id": PROJECT_ID, "key": "zeta"}),
        ),
        candidate(
            key="alpha",
            candidate_id=content_id("cand", {"project_id": PROJECT_ID, "key": "alpha"}),
        ),
    )

    with pytest.raises(ValidationError, match="candidates are sorted"):
        snapshot(
            snapshot_id=content_id("snap", snapshot_payload(projects, candidates)),
            projects=projects,
            candidates=candidates,
        )


def test_candidate_decision_happy_path() -> None:
    value = decision()

    assert value.status == DecisionStatus.INBOX
    assert value.decided_at is None


def test_decided_candidate_requires_decided_at() -> None:
    with pytest.raises(ValidationError, match="decided_at"):
        decision(status=DecisionStatus.ACCEPTED)


def test_decided_candidate_requires_human_ownership() -> None:
    with pytest.raises(ValidationError, match="explicit user approval"):
        decision(status=DecisionStatus.ACCEPTED, decided_at=NOW)


def test_inbox_candidate_rejects_decided_at() -> None:
    with pytest.raises(ValidationError, match="inbox decision"):
        decision(decided_at=NOW)


def test_inbox_candidate_rejects_approval() -> None:
    with pytest.raises(ValidationError, match="inbox decision"):
        decision(approval=Approval(approved_by="user", approved_at=NOW))


def test_content_ids_are_deterministic() -> None:
    first_route = route()
    second_route = route()
    first_candidate = candidate()
    second_candidate = candidate()
    first_snapshot = snapshot()
    second_snapshot = snapshot()

    assert first_route.route_id == second_route.route_id
    assert first_candidate.candidate_id == second_candidate.candidate_id
    assert first_snapshot.snapshot_id == second_snapshot.snapshot_id
