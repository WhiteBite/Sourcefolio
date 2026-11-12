from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sourcefolio.application.registry_ports import RegistryRouteStore, RegistrySnapshotStore
from sourcefolio.domain.common import content_id
from sourcefolio.domain.registry import (
    Candidate,
    CandidateDecision,
    CandidateKind,
    CandidateMention,
    DecisionStatus,
    FitLevel,
    RegistryRoute,
    RegistrySnapshot,
    RouteVerdict,
    SnapshotProject,
)

_FIT_RANK = {FitLevel.LOW: 1, FitLevel.MEDIUM: 2, FitLevel.HIGH: 3}


def _candidate_id(project_id: str, key: str) -> str:
    return content_id("cand", {"project_id": project_id, "key": key})


def _most_frequent_kind(
    mentions: list[tuple[RegistryRoute, CandidateMention]],
) -> CandidateKind:
    counts: dict[CandidateKind, int] = {}
    for _route, mention in mentions:
        counts[mention.kind] = counts.get(mention.kind, 0) + 1
    if not counts:
        return CandidateKind.OTHER
    return max(counts, key=lambda kind: counts[kind])


def _most_frequent_title(
    mentions: list[tuple[RegistryRoute, CandidateMention]],
) -> str:
    counts: dict[str, int] = {}
    for _route, mention in mentions:
        counts[mention.title] = counts.get(mention.title, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda title: (counts[title], len(title)))


@dataclass(frozen=True)
class RegistryBuildQuery:
    dry_run: bool = False


@dataclass(frozen=True)
class RegistryBuildReport:
    snapshot_id: str | None
    snapshot_path: str | None
    latest_path: str | None
    candidates: int
    mentions: int
    projects: dict[str, dict[str, int]]
    merged: int
    stale_accepted: int
    dropped: int
    dry_run: bool


class RegistryBuilder:
    def __init__(
        self,
        *,
        routes: RegistryRouteStore,
        snapshots: RegistrySnapshotStore,
        projects: Mapping[str, str],
        decisions: Iterable[CandidateDecision] = (),
        merges: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._routes = routes
        self._snapshots = snapshots
        self._projects = dict(projects)
        self._decisions = tuple(decisions)
        self._merges = dict(merges or {})
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, query: RegistryBuildQuery) -> RegistryBuildReport:
        routes_list = list(self._routes.iter_all())
        dropped = sum(route.verdict != RouteVerdict.CANDIDATE for route in routes_list)
        insufficient = sum(
            route.verdict == RouteVerdict.INSUFFICIENT_SIGNAL for route in routes_list
        )

        mentions_by_key: dict[tuple[str, str], list[tuple[RegistryRoute, CandidateMention]]] = {}
        for route in routes_list:
            if route.verdict != RouteVerdict.CANDIDATE:
                continue
            for mention in route.mentions:
                key = (route.project_id, mention.candidate_key)
                mentions_by_key.setdefault(key, []).append((route, mention))

        decisions_by_id: dict[str, CandidateDecision] = {}
        for decision in self._decisions:
            decisions_by_id[_candidate_id(decision.project_id, decision.key)] = decision

        merged_from: dict[tuple[str, str], set[str]] = {}
        aliases: dict[tuple[str, str], set[str]] = {}
        for source_id, target_id in sorted(self._merges.items()):
            target = decisions_by_id.get(target_id)
            source = decisions_by_id.get(source_id)
            if target is None or source is None:
                continue
            target_key = (target.project_id, target.key)
            source_key = (source.project_id, source.key)
            if source_key == target_key:
                continue
            moved = mentions_by_key.pop(source_key, [])
            mentions_by_key.setdefault(target_key, []).extend(moved)
            merged_from.setdefault(target_key, set()).add(source_id)
            if source.key != target.key:
                aliases.setdefault(target_key, set()).add(source.key)

        candidates = [
            self._candidate(
                project_id=project_id,
                key=key,
                mentions=group,
                aliases=aliases.get((project_id, key), set()),
                merged_ids=merged_from.get((project_id, key), set()),
            )
            for (project_id, key), group in mentions_by_key.items()
        ]
        built_ids = {candidate.candidate_id for candidate in candidates}

        outcomes = {
            candidate_id: decision.status for candidate_id, decision in decisions_by_id.items()
        }
        stale: list[Candidate] = []
        for candidate_id, decision in decisions_by_id.items():
            if decision.status != DecisionStatus.ACCEPTED or candidate_id in built_ids:
                continue
            stale.append(
                Candidate(
                    candidate_id=candidate_id,
                    project_id=decision.project_id,
                    key=decision.key,
                    kind=decision.kind,
                    title=decision.title,
                    fit=FitLevel.LOW,
                    mention_count=0,
                    stale=True,
                )
            )
        candidates.extend(stale)
        ordered = tuple(sorted(candidates, key=lambda candidate: candidate.candidate_id))

        snapshot_projects: list[SnapshotProject] = []
        report_projects: dict[str, dict[str, int]] = {}
        for project_id in sorted(self._projects):
            project_candidates = [
                candidate for candidate in ordered if candidate.project_id == project_id
            ]
            accepted = sum(
                outcomes.get(candidate.candidate_id) == DecisionStatus.ACCEPTED
                for candidate in project_candidates
            )
            rejected = sum(
                outcomes.get(candidate.candidate_id) == DecisionStatus.REJECTED
                for candidate in project_candidates
            )
            stale_count = sum(candidate.stale for candidate in project_candidates)
            snapshot_projects.append(
                SnapshotProject(
                    project_id=project_id,
                    name=self._projects[project_id],
                    candidate_count=len(project_candidates),
                    accepted=accepted,
                    rejected=rejected,
                    stale=stale_count,
                )
            )
            report_projects[project_id] = {
                "candidates": len(project_candidates),
                "accepted": accepted,
                "rejected": rejected,
                "stale": stale_count,
            }

        payload = {
            "schema_version": "registry-snapshot/v1",
            "projects": [project.model_dump(mode="json") for project in snapshot_projects],
            "candidates": [candidate.model_dump(mode="json") for candidate in ordered],
            "route_count": len(routes_list),
            "insufficient_signal_routes": insufficient,
            "dropped_claims": dropped,
        }
        snapshot_id = content_id("snap", payload)
        snapshot = RegistrySnapshot(
            snapshot_id=snapshot_id,
            generated_at=self._clock(),
            projects=tuple(snapshot_projects),
            candidates=ordered,
            route_count=len(routes_list),
            insufficient_signal_routes=insufficient,
            dropped_claims=dropped,
        )

        snapshot_path: str | None = None
        if not query.dry_run:
            self._snapshots.save(snapshot)
            snapshot_path = f"snapshots/{snapshot_id}.json"

        return RegistryBuildReport(
            snapshot_id=snapshot_id,
            snapshot_path=snapshot_path,
            latest_path=None,
            candidates=len(ordered),
            mentions=sum(candidate.mention_count for candidate in ordered),
            projects=report_projects,
            merged=sum(len(candidate.merged_from) for candidate in ordered),
            stale_accepted=len(stale),
            dropped=dropped,
            dry_run=query.dry_run,
        )

    @staticmethod
    def _candidate(
        *,
        project_id: str,
        key: str,
        mentions: list[tuple[RegistryRoute, CandidateMention]],
        aliases: set[str],
        merged_ids: set[str],
    ) -> Candidate:
        fit = max(
            (mention.fit for _route, mention in mentions),
            key=lambda value: _FIT_RANK[value],
            default=FitLevel.LOW,
        )
        mention_ids = tuple(sorted({mention.mention_id for _route, mention in mentions}))
        source_ids = tuple(sorted({route.source_id for route, _mention in mentions}))
        capture_times = [route.captured_at for route, _mention in mentions]
        return Candidate(
            candidate_id=_candidate_id(project_id, key),
            project_id=project_id,
            key=key,
            kind=_most_frequent_kind(mentions),
            title=_most_frequent_title(mentions) or key,
            fit=fit,
            aliases=tuple(sorted(aliases)),
            merged_from=tuple(sorted(merged_ids)),
            mention_ids=mention_ids,
            source_ids=source_ids,
            mention_count=len(mention_ids),
            first_capture_at=min(capture_times) if capture_times else None,
            last_capture_at=max(capture_times) if capture_times else None,
        )
