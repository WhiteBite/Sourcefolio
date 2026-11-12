from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sourcefolio.adapters.filesystem import FileSystemHumanNoteStore
from sourcefolio.application.candidate_notes import (
    CandidateDecisionReader,
    CandidateDecisionWriter,
    CandidateNoteResult,
)
from sourcefolio.application.frontmatter import parse_frontmatter
from sourcefolio.domain.common import ActorKind
from sourcefolio.domain.registry import CandidateKind, DecisionStatus

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
CANDIDATE_ID = "cand_" + "a" * 64
OTHER_ID = "cand_" + "b" * 64


def fixed_clock() -> datetime:
    return NOW


def build_writer(root: Path) -> CandidateDecisionWriter:
    return CandidateDecisionWriter(notes=FileSystemHumanNoteStore(root), clock=fixed_clock)


def build_reader(root: Path) -> CandidateDecisionReader:
    return CandidateDecisionReader(notes=FileSystemHumanNoteStore(root))


def read_properties(root: Path, candidate_id: str = CANDIDATE_ID) -> Mapping[str, object]:
    content = (root / f"{candidate_id}.md").read_text(encoding="utf-8")
    return parse_frontmatter(content).properties


def write_note(root: Path, content: str, candidate_id: str = CANDIDATE_ID) -> None:
    assert FileSystemHumanNoteStore(root).create_if_absent(f"{candidate_id}.md", content)


def set_candidate(
    writer: CandidateDecisionWriter,
    *,
    candidate_id: str = CANDIDATE_ID,
    status: DecisionStatus = DecisionStatus.ACCEPTED,
    rationale: str = "",
    approved_by: str | None = "mitya",
    merge_into: str | None = None,
) -> CandidateNoteResult:
    return writer.set(
        candidate_id=candidate_id,
        project_id="sourcefolio",
        key="tool:gallery-dl",
        kind=CandidateKind.TOOL,
        title="gallery-dl",
        status=status,
        rationale=rationale,
        approved_by=approved_by,
        merge_into=merge_into,
    )


def test_accepted_write_sets_fields(tmp_path: Path) -> None:
    result = set_candidate(build_writer(tmp_path), rationale="separate slideshow path")

    assert result.created is True
    assert result.note == f"{CANDIDATE_ID}.md"
    assert result.applied == {
        "status": "accepted",
        "rationale": "separate slideshow path",
        "decided_at": NOW.isoformat(),
        "approved_by": "mitya",
        "approval_at": NOW.isoformat(),
    }
    properties = read_properties(tmp_path)
    assert properties["type"] == "candidate"
    assert properties["candidate_id"] == CANDIDATE_ID
    assert properties["project_id"] == "sourcefolio"
    assert properties["key"] == "tool:gallery-dl"
    assert properties["kind"] == "tool"
    assert properties["title"] == "gallery-dl"
    assert properties["status"] == "accepted"
    assert properties["rationale"] == "separate slideshow path"
    assert properties["decided_at"] == NOW.isoformat()
    assert properties["approved_by"] == "mitya"
    assert properties["approval_at"] == NOW.isoformat()


def test_second_write_preserves_body(tmp_path: Path) -> None:
    writer = build_writer(tmp_path)
    set_candidate(writer)

    result = set_candidate(writer, status=DecisionStatus.DEFERRED)

    assert result.created is False
    content = (tmp_path / f"{CANDIDATE_ID}.md").read_text(encoding="utf-8")
    assert content.count("## Решение") == 1
    assert content.endswith("\n## Решение\n")
    assert "status: deferred" in content


def test_missing_approved_by_raises_without_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        set_candidate(build_writer(tmp_path), approved_by=None)

    assert not (tmp_path / f"{CANDIDATE_ID}.md").exists()


def test_inbox_clears_decided_fields_and_ignores_approved_by(tmp_path: Path) -> None:
    writer = build_writer(tmp_path)
    set_candidate(writer)

    result = set_candidate(writer, status=DecisionStatus.INBOX, approved_by="someone")

    assert result.applied == {
        "status": "inbox",
        "decided_at": None,
        "approved_by": None,
        "approval_at": None,
    }
    properties = read_properties(tmp_path)
    assert properties["status"] == "inbox"
    assert "decided_at" not in properties
    assert "approved_by" not in properties
    assert "approval_at" not in properties


def test_rationale_rendered_only_when_provided(tmp_path: Path) -> None:
    writer = build_writer(tmp_path)
    set_candidate(writer)
    set_candidate(writer, candidate_id=OTHER_ID, rationale="   ")

    assert "rationale" not in read_properties(tmp_path)
    assert "rationale" not in read_properties(tmp_path, OTHER_ID)

    set_candidate(writer, candidate_id=OTHER_ID, rationale="separate slideshow path")

    assert read_properties(tmp_path, OTHER_ID)["rationale"] == "separate slideshow path"


def test_iter_decisions_round_trip(tmp_path: Path) -> None:
    set_candidate(build_writer(tmp_path), rationale="separate slideshow path")

    decisions = list(build_reader(tmp_path).iter_decisions())

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.candidate_id == CANDIDATE_ID
    assert decision.project_id == "sourcefolio"
    assert decision.key == "tool:gallery-dl"
    assert decision.kind == CandidateKind.TOOL
    assert decision.title == "gallery-dl"
    assert decision.status == DecisionStatus.ACCEPTED
    assert decision.rationale == "separate slideshow path"
    assert decision.decided_at == NOW
    assert decision.provenance.actor_kind == ActorKind.USER
    assert decision.provenance.actor_id == "mitya"
    assert decision.provenance.method == "candidate-note"
    assert decision.approval is not None
    assert decision.approval.approved_by == "mitya"
    assert decision.approval.approved_at == NOW


def test_merges_reports_override(tmp_path: Path) -> None:
    result = set_candidate(
        build_writer(tmp_path),
        status=DecisionStatus.INBOX,
        approved_by=None,
        merge_into=OTHER_ID,
    )

    assert result.applied == {"merged_into": OTHER_ID}
    assert build_reader(tmp_path).merges() == {CANDIDATE_ID: OTHER_ID}
    assert read_properties(tmp_path)["status"] == "inbox"


def test_self_merge_raises_without_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        set_candidate(
            build_writer(tmp_path),
            status=DecisionStatus.INBOX,
            approved_by=None,
            merge_into=CANDIDATE_ID,
        )

    assert not (tmp_path / f"{CANDIDATE_ID}.md").exists()


def test_non_candidate_note_is_ignored(tmp_path: Path) -> None:
    write_note(
        tmp_path,
        "---\n"
        "type: idea\n"
        f"candidate_id: {CANDIDATE_ID}\n"
        f"merged_into: {OTHER_ID}\n"
        "---\n"
        "\n"
        "## Решение\n",
    )

    reader = build_reader(tmp_path)
    assert list(reader.iter_decisions()) == []
    assert reader.merges() == {}


def test_broken_yaml_is_skipped(tmp_path: Path) -> None:
    write_note(
        tmp_path,
        "---\ntype: candidate\ncandidate_id: [unclosed\n---\n\n## Решение\n",
    )

    with pytest.warns(UserWarning):
        assert list(build_reader(tmp_path).iter_decisions()) == []
