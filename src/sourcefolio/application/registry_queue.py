from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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
_STATUS_RANK = {
    DecisionStatus.ACCEPTED: 0,
    DecisionStatus.INBOX: 1,
    DecisionStatus.DEFERRED: 2,
    DecisionStatus.REJECTED: 3,
}
_ACCEPTED_WITH_OUTCOME_RANK = 4


@dataclass(frozen=True)
class QueueMention:
    source_id: str
    capture_id: str
    card_capture_id: str
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
    status: str
    outcome: str | None = None
    outcome_at: str | None = None
    stale: bool = False
    mentions: tuple[QueueMention, ...] = ()


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


def _effective_status_rank(status: DecisionStatus, outcome: str | None) -> int:
    if status == DecisionStatus.ACCEPTED and outcome is not None:
        return _ACCEPTED_WITH_OUTCOME_RANK
    return _STATUS_RANK[status]


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
        _effective_status_rank(status, queued.outcome),
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
        decision_provider: Callable[[], Iterable[CandidateDecision]] | None = None,
        latest_captures: Mapping[str, str] | None = None,
        latest_captures_provider: Callable[[], Mapping[str, str]] | None = None,
        projects: Mapping[str, str] | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._routes = routes
        self._decisions = tuple(decisions)
        self._decision_provider = decision_provider
        self._latest_captures = latest_captures
        self._latest_captures_provider = latest_captures_provider
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

        decisions = {decision.candidate_id: decision for decision in self._resolve_decisions()}
        latest_captures = self._resolve_latest_captures()
        grouped: dict[str, list[tuple[Candidate, QueueCandidate, DecisionStatus]]] = {}
        for candidate in snapshot.candidates:
            if allowed_projects and candidate.project_id not in allowed_projects:
                continue
            decision = decisions.get(candidate.candidate_id)
            candidate_status = decision.status if decision is not None else DecisionStatus.INBOX
            if status is not None and candidate_status != status:
                continue
            mentions, score = self._mentions(candidate, mention_index, latest_captures)
            queued = QueueCandidate(
                candidate_id=candidate.candidate_id,
                title=candidate.title,
                kind=candidate.kind,
                key=candidate.key,
                fit=candidate.fit,
                mention_count=candidate.mention_count,
                source_count=len(candidate.source_ids),
                score=score,
                status=candidate_status.value,
                outcome=decision.outcome if decision is not None else None,
                outcome_at=(
                    decision.outcome_at.isoformat()
                    if decision is not None and decision.outcome_at is not None
                    else None
                ),
                stale=candidate.stale,
                mentions=mentions,
            )
            grouped.setdefault(candidate.project_id, []).append(
                (candidate, queued, candidate_status)
            )

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

    def _resolve_decisions(self) -> Iterable[CandidateDecision]:
        if self._decision_provider is not None:
            return self._decision_provider()
        return self._decisions

    def _resolve_latest_captures(self) -> Mapping[str, str]:
        if self._latest_captures_provider is not None:
            return self._latest_captures_provider()
        return self._latest_captures or {}

    def _project_name(self, project_id: str) -> str:
        if self._projects is None:
            return project_id
        return self._projects.get(project_id, project_id)

    @staticmethod
    def _mentions(
        candidate: Candidate,
        index: Mapping[str, tuple[RegistryRoute, CandidateMention]],
        latest_captures: Mapping[str, str],
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
                        card_capture_id=latest_captures.get(route.source_id, route.capture_id),
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
