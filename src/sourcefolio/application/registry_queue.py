from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sourcefolio.application.registry_ports import RegistryRouteStore, RegistrySnapshotStore
from sourcefolio.domain.registry import (
    Candidate,
    CandidateDecision,
    CandidateKind,
    CandidateMention,
    DecisionStatus,
    FitLevel,
    RegistryRoute,
    RouteVerdict,
)

_FIT_RANK = {FitLevel.LOW: 1, FitLevel.MEDIUM: 2, FitLevel.HIGH: 3}
_OUTCOME_RANK = {
    DecisionStatus.ACCEPTED: 0,
    DecisionStatus.INBOX: 1,
    DecisionStatus.DEFERRED: 2,
    DecisionStatus.REJECTED: 3,
}


@dataclass(frozen=True)
class QueueMention:
    source_id: str
    capture_id: str
    url: str
    title: str
    quote: str
    field: str
    fit: FitLevel


@dataclass(frozen=True)
class QueueCandidate:
    candidate_id: str
    title: str
    kind: CandidateKind
    key: str
    fit: FitLevel
    mention_count: int
    source_count: int
    score: int
    outcome: str
    mentions: tuple[QueueMention, ...]


@dataclass(frozen=True)
class ProjectQueue:
    project_id: str
    name: str
    candidates: tuple[QueueCandidate, ...]


@dataclass(frozen=True)
class RegistryQueue:
    snapshot_id: str | None
    generated_at: str | None
    projects: tuple[ProjectQueue, ...]


def _queue_order_key(
    entry: tuple[Candidate, QueueCandidate, DecisionStatus],
) -> tuple[int, int, int, int, float, str]:
    candidate, queued, status = entry
    recency = (
        -candidate.last_capture_at.timestamp()
        if candidate.last_capture_at is not None
        else float("inf")
    )
    return (
        _OUTCOME_RANK[status],
        -_FIT_RANK[queued.fit],
        -queued.score,
        -queued.mention_count,
        recency,
        queued.candidate_id,
    )


class RegistryQueueBuilder:
    def __init__(
        self,
        *,
        snapshots: RegistrySnapshotStore,
        routes: RegistryRouteStore,
        decisions: Iterable[CandidateDecision] = (),
        projects: Mapping[str, str] | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._routes = routes
        self._decisions = tuple(decisions)
        self._projects = dict(projects) if projects is not None else None

    def build(
        self,
        *,
        project_ids: Iterable[str] = (),
        status: DecisionStatus | None = None,
        limit: int | None = None,
    ) -> RegistryQueue:
        snapshot = self._snapshots.load_latest()
        if snapshot is None:
            return RegistryQueue(snapshot_id=None, generated_at=None, projects=())

        allowed_projects = set(project_ids)
        mention_index: dict[str, tuple[RegistryRoute, CandidateMention]] = {}
        for route in self._routes.iter_all():
            if route.verdict != RouteVerdict.CANDIDATE:
                continue
            for mention in route.mentions:
                mention_index[mention.mention_id] = (route, mention)

        outcomes = {decision.candidate_id: decision.status for decision in self._decisions}
        grouped: dict[str, list[tuple[Candidate, QueueCandidate, DecisionStatus]]] = {}
        for candidate in snapshot.candidates:
            if allowed_projects and candidate.project_id not in allowed_projects:
                continue
            outcome = outcomes.get(candidate.candidate_id, DecisionStatus.INBOX)
            if status is not None and outcome != status:
                continue
            mentions, score = self._mentions(candidate, mention_index)
            queued = QueueCandidate(
                candidate_id=candidate.candidate_id,
                title=candidate.title,
                kind=candidate.kind,
                key=candidate.key,
                fit=candidate.fit,
                mention_count=candidate.mention_count,
                source_count=len(candidate.source_ids),
                score=score,
                outcome=outcome.value,
                mentions=mentions,
            )
            grouped.setdefault(candidate.project_id, []).append((candidate, queued, outcome))

        projects_out: list[ProjectQueue] = []
        for project_id in sorted(grouped):
            ordered = sorted(grouped[project_id], key=_queue_order_key)
            if limit is not None:
                ordered = ordered[:limit]
            projects_out.append(
                ProjectQueue(
                    project_id=project_id,
                    name=self._project_name(project_id),
                    candidates=tuple(item[1] for item in ordered),
                )
            )

        return RegistryQueue(
            snapshot_id=snapshot.snapshot_id,
            generated_at=snapshot.generated_at.isoformat(),
            projects=tuple(projects_out),
        )

    def _project_name(self, project_id: str) -> str:
        if self._projects is None:
            return project_id
        return self._projects.get(project_id, project_id)

    @staticmethod
    def _mentions(
        candidate: Candidate,
        index: Mapping[str, tuple[RegistryRoute, CandidateMention]],
    ) -> tuple[tuple[QueueMention, ...], int]:
        resolved = [
            index[mention_id] for mention_id in candidate.mention_ids if mention_id in index
        ]
        mentions = tuple(
            sorted(
                (
                    QueueMention(
                        source_id=route.source_id,
                        capture_id=route.capture_id,
                        url=route.source_url,
                        title=route.source_title,
                        quote=mention.anchor.quote,
                        field=mention.anchor.field.value,
                        fit=mention.fit,
                    )
                    for route, mention in resolved
                ),
                key=lambda item: (-_FIT_RANK[item.fit], item.quote, item.source_id),
            )
        )
        score = max((route.pre_rank for route, _mention in resolved), default=0)
        return mentions, score
