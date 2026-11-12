from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime

import yaml
from pydantic import ValidationError

from sourcefolio.application.frontmatter import parse_frontmatter
from sourcefolio.application.projection_ports import HumanNoteReader, HumanNoteStore
from sourcefolio.domain.common import ActorKind, Approval, Provenance
from sourcefolio.domain.registry import CandidateDecision, CandidateKind, DecisionStatus


@dataclass(frozen=True)
class CandidateNoteResult:
    note: str
    created: bool
    applied: dict[str, object | None]


def render_candidate_note(decision: CandidateDecision) -> str:
    properties: dict[str, object] = {
        "type": "candidate",
        "candidate_id": decision.candidate_id,
        "project_id": decision.project_id,
        "key": decision.key,
        "kind": decision.kind.value,
        "title": decision.title,
        "status": decision.status.value,
    }
    if decision.rationale:
        properties["rationale"] = decision.rationale
    if decision.decided_at is not None:
        properties["decided_at"] = decision.decided_at.isoformat()
    if decision.approval is not None:
        properties["approved_by"] = decision.approval.approved_by
        properties["approval_at"] = decision.approval.approved_at.isoformat()
    frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
    lines = ["---", frontmatter, "---", "", "## Решение", ""]
    return "\n".join(lines)


class CandidateDecisionWriter:
    def __init__(self, *, notes: HumanNoteStore, clock: Callable[[], datetime]) -> None:
        self._notes = notes
        self._clock = clock

    def set(
        self,
        *,
        candidate_id: str,
        project_id: str,
        key: str,
        kind: CandidateKind,
        title: str,
        status: DecisionStatus,
        rationale: str = "",
        approved_by: str | None = None,
        merge_into: str | None = None,
    ) -> CandidateNoteResult:
        if merge_into == candidate_id:
            raise ValueError("candidate cannot be merged into itself")
        updates: dict[str, object | None]
        if merge_into is not None:
            updates = {"merged_into": merge_into}
        elif status == DecisionStatus.INBOX:
            updates = {
                "status": "inbox",
                "decided_at": None,
                "approved_by": None,
                "approval_at": None,
            }
            if rationale.strip():
                updates["rationale"] = rationale
        else:
            if approved_by is None or not approved_by.strip():
                raise ValueError("decided candidate requires approved_by")
            now = self._clock()
            updates = {"status": status.value}
            if rationale.strip():
                updates["rationale"] = rationale
            updates["decided_at"] = now.isoformat()
            updates["approved_by"] = approved_by.strip()
            updates["approval_at"] = now.isoformat()
        path = f"{candidate_id}.md"
        base = CandidateDecision(
            candidate_id=candidate_id,
            project_id=project_id,
            key=key,
            kind=kind,
            title=title,
            provenance=Provenance(
                actor_kind=ActorKind.USER,
                actor_id="user",
                method="candidate-note",
            ),
            approval=None,
        )
        created = self._notes.create_if_absent(path, render_candidate_note(base))
        if not self._notes.update_properties(path, updates):
            raise ValueError(f"candidate note is unreadable: {path}")
        return CandidateNoteResult(note=path, created=created, applied=dict(updates))


class CandidateDecisionReader:
    def __init__(self, *, notes: HumanNoteReader) -> None:
        self._notes = notes

    def iter_decisions(self) -> Iterator[CandidateDecision]:
        for _path, content in self._notes.iter_notes():
            properties = parse_frontmatter(content).properties
            if properties.get("type") != "candidate":
                continue
            try:
                yield self._decision(properties)
            except (ValueError, TypeError, ValidationError):
                continue

    def merges(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for _path, content in self._notes.iter_notes():
            properties = parse_frontmatter(content).properties
            if properties.get("type") != "candidate":
                continue
            merged_into = properties.get("merged_into")
            candidate_id = properties.get("candidate_id")
            if (
                isinstance(merged_into, str)
                and merged_into
                and isinstance(candidate_id, str)
                and candidate_id
            ):
                merged[candidate_id] = merged_into
        return merged

    @staticmethod
    def _decision(properties: Mapping[str, object]) -> CandidateDecision:
        approved_by = properties.get("approved_by")
        approval_at = properties.get("approval_at")
        approval = (
            Approval(approved_by=str(approved_by), approved_at=str(approval_at))
            if approved_by is not None and approval_at is not None
            else None
        )
        decided_at = properties.get("decided_at")
        rationale = properties.get("rationale")
        return CandidateDecision(
            candidate_id=str(properties.get("candidate_id", "")),
            project_id=str(properties.get("project_id", "")),
            key=str(properties.get("key", "")),
            kind=CandidateKind(str(properties.get("kind", ""))),
            title=str(properties.get("title", "")),
            status=DecisionStatus(str(properties.get("status", DecisionStatus.INBOX.value))),
            rationale="" if rationale is None else str(rationale),
            decided_at=None if decided_at is None else str(decided_at),
            provenance=Provenance(
                actor_kind=ActorKind.USER,
                actor_id=str(approved_by or "user"),
                method="candidate-note",
            ),
            approval=approval,
        )
