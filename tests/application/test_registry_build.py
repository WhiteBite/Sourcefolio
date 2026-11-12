from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime

from sourcefolio.application.registry_build import RegistryBuilder, RegistryBuildQuery
from sourcefolio.domain.common import ActorKind, Approval, Provenance, content_id
from sourcefolio.domain.registry import (
    AnchorField,
    CandidateDecision,
    CandidateKind,
    CandidateMention,
    DecisionStatus,
    FitLevel,
    MentionAnchor,
    RegistryRoute,
    RegistrySnapshot,
    RouteVerdict,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PROMPT_FINGERPRINT = "0123456789abcdef"
MODEL = "test-model"


class FakeRouteStore:
    def __init__(self, routes: Iterable[RegistryRoute] = ()) -> None:
        self._routes = {route.route_id: route for route in routes}

    def get(self, route_id: str) -> RegistryRoute | None:
        return self._routes.get(route_id)

    def save(self, route: RegistryRoute) -> bool:
        created = route.route_id not in self._routes
        self._routes[route.route_id] = route
        return created

    def iter_all(self) -> Iterator[RegistryRoute]:
        return iter(tuple(self._routes.values()))


class FakeSnapshotStore:
    def __init__(self) -> None:
        self.saved: list[RegistrySnapshot] = []

    def save(self, snapshot: RegistrySnapshot) -> bool:
        if any(item.snapshot_id == snapshot.snapshot_id for item in self.saved):
            return False
        self.saved.append(snapshot)
        return True

    def load(self, snapshot_id: str) -> RegistrySnapshot | None:
        return next((item for item in self.saved if item.snapshot_id == snapshot_id), None)

    def load_latest(self) -> RegistrySnapshot | None:
        return self.saved[-1] if self.saved else None

    def iter_snapshots(self) -> Iterator[RegistrySnapshot]:
        return iter(tuple(self.saved))


def source_id(index: int) -> str:
    return "src_" + f"{index:064d}"


def capture_id(index: int) -> str:
    return "cap_" + f"{index + 100:064d}"


def make_mention(
    *,
    key: str = "owner/repo",
    title: str = "Example tool",
    kind: CandidateKind = CandidateKind.TOOL,
    fit: FitLevel = FitLevel.HIGH,
    quote: str = "useful example tool",
) -> CandidateMention:
    payload: dict[str, object] = {
        "candidate_key": key,
        "kind": kind,
        "title": title,
        "fit": fit,
        "anchor": MentionAnchor(
            field=AnchorField.DESCRIPTION,
            quote=quote,
            start_char=0,
            end_char=len(quote),
        ).model_dump(mode="json"),
    }
    return CandidateMention(mention_id=content_id("men", payload), **payload)


def make_route(
    *,
    index: int,
    project_id: str = "sourcefolio",
    mentions: tuple[CandidateMention, ...] = (),
    verdict: RouteVerdict = RouteVerdict.CANDIDATE,
    reason: str = "",
) -> RegistryRoute:
    identity = {
        "source_id": source_id(index),
        "capture_id": capture_id(index),
        "project_id": project_id,
        "model": MODEL,
        "prompt_fingerprint": PROMPT_FINGERPRINT,
    }
    return RegistryRoute(
        route_id=content_id("rt", identity),
        source_id=identity["source_id"],
        capture_id=identity["capture_id"],
        project_id=project_id,
        model=MODEL,
        prompt_fingerprint=PROMPT_FINGERPRINT,
        verdict=verdict,
        reason=reason,
        mentions=mentions,
        captured_at=NOW,
        provenance=Provenance(actor_kind=ActorKind.AGENT, actor_id="agent", method="registry-test"),
        created_at=NOW,
    )


def make_decision(
    *,
    project_id: str = "sourcefolio",
    key: str = "owner/repo",
    title: str = "Example tool",
    status: DecisionStatus = DecisionStatus.INBOX,
) -> CandidateDecision:
    candidate_id = content_id("cand", {"project_id": project_id, "key": key})
    if status == DecisionStatus.INBOX:
        return CandidateDecision(
            candidate_id=candidate_id,
            project_id=project_id,
            key=key,
            kind=CandidateKind.TOOL,
            title=title,
            status=status,
            provenance=Provenance(
                actor_kind=ActorKind.USER, actor_id="user", method="candidate-note"
            ),
        )
    return CandidateDecision(
        candidate_id=candidate_id,
        project_id=project_id,
        key=key,
        kind=CandidateKind.TOOL,
        title=title,
        status=status,
        decided_at=NOW,
        provenance=Provenance(actor_kind=ActorKind.USER, actor_id="user", method="candidate-note"),
        approval=Approval(approved_by="user", approved_at=NOW),
    )


def make_builder(
    routes: Iterable[RegistryRoute],
    *,
    decisions: Iterable[CandidateDecision] = (),
    merges: Mapping[str, str] | None = None,
    projects: Mapping[str, str] | None = None,
) -> tuple[RegistryBuilder, FakeSnapshotStore]:
    store = FakeSnapshotStore()
    builder = RegistryBuilder(
        routes=FakeRouteStore(routes),
        snapshots=store,
        projects=projects if projects is not None else {"sourcefolio": "Sourcefolio"},
        decisions=decisions,
        merges=merges,
        clock=lambda: NOW,
    )
    return builder, store


def test_same_key_across_captures_yields_one_candidate() -> None:
    routes = [
        make_route(index=1, mentions=(make_mention(quote="useful example tool one"),)),
        make_route(index=2, mentions=(make_mention(quote="useful example tool two"),)),
        make_route(index=3, mentions=(make_mention(quote="useful example tool three"),)),
    ]
    builder, store = make_builder(routes)

    report = builder.run(RegistryBuildQuery())

    assert report.candidates == 1
    assert report.mentions == 3
    candidate = store.saved[0].candidates[0]
    assert candidate.candidate_id == content_id(
        "cand", {"project_id": "sourcefolio", "key": "owner/repo"}
    )
    assert candidate.mention_count == 3
    assert len(candidate.source_ids) == 3
    assert candidate.first_capture_at == NOW
    assert candidate.last_capture_at == NOW


def test_repeated_builds_are_deterministic() -> None:
    routes = [
        make_route(index=1, mentions=(make_mention(quote="first mention text"),)),
        make_route(index=2, mentions=(make_mention(quote="second mention text"),)),
    ]
    first_builder, first_store = make_builder(routes)
    second_builder, second_store = make_builder(routes)

    first = first_builder.run(RegistryBuildQuery())
    second = second_builder.run(RegistryBuildQuery())
    first_builder.run(RegistryBuildQuery())

    assert first.snapshot_id == second.snapshot_id
    assert first.snapshot_path == second.snapshot_path
    assert [item.candidate_id for item in first_store.saved[0].candidates] == [
        item.candidate_id for item in second_store.saved[0].candidates
    ]
    assert len(first_store.saved) == 1


def test_human_merge_collapses_source_into_target() -> None:
    routes = [
        make_route(index=1, mentions=(make_mention(key="owner/repo", quote="primary target"),)),
        make_route(index=2, mentions=(make_mention(key="legacy/tool", quote="legacy source"),)),
    ]
    target_id = content_id("cand", {"project_id": "sourcefolio", "key": "owner/repo"})
    source_candidate_id = content_id("cand", {"project_id": "sourcefolio", "key": "legacy/tool"})
    decisions = [make_decision(key="owner/repo"), make_decision(key="legacy/tool")]
    builder, store = make_builder(
        routes,
        decisions=decisions,
        merges={source_candidate_id: target_id},
    )

    report = builder.run(RegistryBuildQuery())

    assert report.candidates == 1
    assert report.merged == 1
    candidate = store.saved[0].candidates[0]
    assert candidate.candidate_id == target_id
    assert candidate.merged_from == (source_candidate_id,)
    assert candidate.aliases == ("legacy/tool",)
    assert candidate.mention_count == 2


def test_accepted_candidate_without_mentions_survives_as_stale() -> None:
    routes = [make_route(index=1, mentions=(make_mention(quote="current mention text"),))]
    retired_id = content_id("cand", {"project_id": "sourcefolio", "key": "retired/tool"})
    decisions = [
        make_decision(
            key="retired/tool",
            title="Retired tool",
            status=DecisionStatus.ACCEPTED,
        )
    ]
    builder, store = make_builder(routes, decisions=decisions)

    report = builder.run(RegistryBuildQuery())

    assert report.stale_accepted == 1
    snapshot = store.saved[0]
    stale = next(item for item in snapshot.candidates if item.candidate_id == retired_id)
    assert stale.stale is True
    assert stale.mention_count == 0
    assert stale.first_capture_at is None
    assert stale.last_capture_at is None
    assert report.projects["sourcefolio"]["stale"] == 1


def test_same_key_in_two_projects_stays_separate() -> None:
    routes = [
        make_route(index=1, project_id="midas", mentions=(make_mention(quote="midas mention"),)),
        make_route(index=2, project_id="mafiin", mentions=(make_mention(quote="mafiin mention"),)),
    ]
    builder, store = make_builder(
        routes,
        projects={"midas": "MidasAI", "mafiin": "Mafiin"},
    )

    report = builder.run(RegistryBuildQuery())

    assert report.candidates == 2
    assert {item.candidate_id for item in store.saved[0].candidates} == {
        content_id("cand", {"project_id": "midas", "key": "owner/repo"}),
        content_id("cand", {"project_id": "mafiin", "key": "owner/repo"}),
    }


def test_snapshot_project_counts_and_sorting() -> None:
    routes = [
        make_route(index=1, project_id="zeta", mentions=(make_mention(quote="zeta mention"),)),
        make_route(index=2, project_id="alpha", mentions=(make_mention(quote="alpha mention"),)),
    ]
    decisions = [
        make_decision(project_id="alpha", status=DecisionStatus.ACCEPTED),
        make_decision(project_id="zeta", status=DecisionStatus.REJECTED),
    ]
    builder, store = make_builder(
        routes,
        decisions=decisions,
        projects={"zeta": "Zeta", "alpha": "Alpha"},
    )

    report = builder.run(RegistryBuildQuery())

    snapshot = store.saved[0]
    assert [project.project_id for project in snapshot.projects] == ["alpha", "zeta"]
    assert [project.name for project in snapshot.projects] == ["Alpha", "Zeta"]
    assert snapshot.projects[0].candidate_count == 1
    assert snapshot.projects[0].accepted == 1
    assert snapshot.projects[1].rejected == 1
    assert report.projects["alpha"] == {
        "candidates": 1,
        "accepted": 1,
        "rejected": 0,
        "stale": 0,
    }


def test_dry_run_saves_nothing() -> None:
    routes = [make_route(index=1, mentions=(make_mention(quote="dry run mention"),))]
    builder, store = make_builder(routes)

    report = builder.run(RegistryBuildQuery(dry_run=True))

    assert store.saved == []
    assert report.dry_run is True
    assert report.snapshot_id is not None
    assert report.snapshot_path is None
    assert report.latest_path is None
    assert report.candidates == 1


def test_non_candidate_routes_are_dropped() -> None:
    routes = [
        make_route(index=1, mentions=(make_mention(quote="candidate mention"),)),
        make_route(index=2, verdict=RouteVerdict.IRRELEVANT),
        make_route(index=3, verdict=RouteVerdict.INSUFFICIENT_SIGNAL, reason="no signal"),
    ]
    builder, store = make_builder(routes)

    report = builder.run(RegistryBuildQuery())

    assert report.dropped == 2
    assert report.candidates == 1
    snapshot = store.saved[0]
    assert snapshot.route_count == 3
    assert snapshot.dropped_claims == 2
    assert snapshot.insufficient_signal_routes == 1
