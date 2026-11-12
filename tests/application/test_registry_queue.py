from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta

from sourcefolio.application.registry_build import RegistryBuilder, RegistryBuildQuery
from sourcefolio.application.registry_queue import (
    QueueMention,
    RegistryQueue,
    RegistryQueueBuilder,
)
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
PROJECTS = {"midas": "MidasAI", "mafiin": "Mafiin"}


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
    def __init__(self, snapshot: RegistrySnapshot | None = None) -> None:
        self.saved: list[RegistrySnapshot] = [] if snapshot is None else [snapshot]

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
    project_id: str = "midas",
    mentions: tuple[CandidateMention, ...] = (),
    verdict: RouteVerdict = RouteVerdict.CANDIDATE,
    reason: str = "",
    pre_rank: int = 0,
    captured_at: datetime = NOW,
    source_url: str = "",
    source_title: str = "",
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
        source_url=source_url,
        source_title=source_title,
        project_id=project_id,
        model=MODEL,
        prompt_fingerprint=PROMPT_FINGERPRINT,
        verdict=verdict,
        reason=reason,
        mentions=mentions,
        pre_rank=pre_rank,
        captured_at=captured_at,
        provenance=Provenance(actor_kind=ActorKind.AGENT, actor_id="agent", method="registry-test"),
        created_at=NOW,
    )


def make_decision(
    *,
    project_id: str = "midas",
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


def build_snapshot(
    routes: Iterable[RegistryRoute],
    *,
    decisions: Iterable[CandidateDecision] = (),
    projects: dict[str, str] | None = None,
) -> RegistrySnapshot:
    store = FakeSnapshotStore()
    builder = RegistryBuilder(
        routes=FakeRouteStore(routes),
        snapshots=store,
        projects=projects if projects is not None else PROJECTS,
        decisions=decisions,
        clock=lambda: NOW,
    )
    builder.run(RegistryBuildQuery())
    return store.saved[-1]


def make_queue_builder(
    snapshot: RegistrySnapshot,
    routes: Iterable[RegistryRoute],
    *,
    decisions: Iterable[CandidateDecision] = (),
    projects: dict[str, str] | None = None,
) -> RegistryQueueBuilder:
    return RegistryQueueBuilder(
        snapshots=FakeSnapshotStore(snapshot),
        routes=FakeRouteStore(routes),
        decisions=decisions,
        projects=projects if projects is not None else PROJECTS,
    )


def candidate_keys(queue: RegistryQueue, project_id: str = "midas") -> list[str]:
    project = next(item for item in queue.projects if item.project_id == project_id)
    return [candidate.key for candidate in project.candidates]


def test_accepted_beats_higher_score_inbox_and_rejected_last() -> None:
    routes = [
        make_route(
            index=1,
            mentions=(
                make_mention(key="tool/accepted", quote="accepted mention", fit=FitLevel.LOW),
            ),
            pre_rank=0,
        ),
        make_route(
            index=2,
            mentions=(make_mention(key="tool/inbox", quote="inbox mention", fit=FitLevel.HIGH),),
            pre_rank=100,
        ),
        make_route(
            index=3,
            mentions=(
                make_mention(key="tool/rejected", quote="rejected mention", fit=FitLevel.HIGH),
            ),
            pre_rank=200,
        ),
    ]
    decisions = [
        make_decision(key="tool/accepted", status=DecisionStatus.ACCEPTED),
        make_decision(key="tool/rejected", status=DecisionStatus.REJECTED),
    ]
    snapshot = build_snapshot(routes, decisions=decisions)

    queue = make_queue_builder(snapshot, routes, decisions=decisions).build()

    assert candidate_keys(queue) == ["tool/accepted", "tool/inbox", "tool/rejected"]
    assert queue.projects[0].name == "MidasAI"


def test_ties_break_by_fit_then_score_then_mentions_then_recency_then_id() -> None:
    routes = [
        make_route(
            index=1,
            mentions=(make_mention(key="fit/high", quote="high fit mention", fit=FitLevel.HIGH),),
            pre_rank=7,
        ),
        make_route(
            index=2,
            mentions=(make_mention(key="fit/low", quote="low fit mention", fit=FitLevel.LOW),),
            pre_rank=7,
        ),
        make_route(
            index=3,
            mentions=(
                make_mention(key="score/high", quote="high score mention", fit=FitLevel.HIGH),
            ),
            pre_rank=9,
        ),
        make_route(
            index=4,
            mentions=(make_mention(key="score/low", quote="low score mention", fit=FitLevel.HIGH),),
            pre_rank=2,
        ),
        make_route(
            index=5,
            mentions=(
                make_mention(key="count/many", quote="many mention one", fit=FitLevel.MEDIUM),
            ),
            pre_rank=4,
        ),
        make_route(
            index=6,
            mentions=(
                make_mention(key="count/many", quote="many mention two", fit=FitLevel.MEDIUM),
            ),
            pre_rank=4,
        ),
        make_route(
            index=7,
            mentions=(make_mention(key="count/one", quote="single mention", fit=FitLevel.MEDIUM),),
            pre_rank=4,
        ),
    ]
    snapshot = build_snapshot(routes)
    queue = make_queue_builder(snapshot, routes).build()

    fit_order = [key for key in candidate_keys(queue) if key.startswith("fit/")]
    assert fit_order == ["fit/high", "fit/low"]
    score_order = [key for key in candidate_keys(queue) if key.startswith("score/")]
    assert score_order == ["score/high", "score/low"]
    count_order = [key for key in candidate_keys(queue) if key.startswith("count/")]
    assert count_order == ["count/many", "count/one"]


def test_recency_breaks_remaining_ties() -> None:
    routes = [
        make_route(
            index=1,
            mentions=(make_mention(key="recent/tool", quote="recent mention", fit=FitLevel.HIGH),),
            pre_rank=3,
            captured_at=NOW,
        ),
        make_route(
            index=2,
            mentions=(make_mention(key="old/tool", quote="old mention", fit=FitLevel.HIGH),),
            pre_rank=3,
            captured_at=NOW - timedelta(days=1),
        ),
    ]
    snapshot = build_snapshot(routes)

    queue = make_queue_builder(snapshot, routes).build()

    assert candidate_keys(queue) == ["recent/tool", "old/tool"]


def test_candidate_id_breaks_final_tie() -> None:
    routes = [
        make_route(
            index=1,
            mentions=(make_mention(key="tool/aaa", quote="first mention", fit=FitLevel.HIGH),),
        ),
        make_route(
            index=2,
            mentions=(make_mention(key="tool/zzz", quote="second mention", fit=FitLevel.HIGH),),
        ),
    ]
    snapshot = build_snapshot(routes)

    queue = make_queue_builder(snapshot, routes).build()

    expected = sorted(
        content_id("cand", {"project_id": "midas", "key": key}) for key in ("tool/aaa", "tool/zzz")
    )
    assert [candidate.candidate_id for candidate in queue.projects[0].candidates] == expected


def test_projects_are_ordered_independently() -> None:
    routes = [
        make_route(
            index=1,
            project_id="midas",
            mentions=(
                make_mention(key="midas/high", quote="midas high mention", fit=FitLevel.HIGH),
            ),
            pre_rank=1,
        ),
        make_route(
            index=2,
            project_id="midas",
            mentions=(make_mention(key="midas/low", quote="midas low mention", fit=FitLevel.LOW),),
            pre_rank=0,
        ),
        make_route(
            index=3,
            project_id="mafiin",
            mentions=(
                make_mention(key="mafiin/high", quote="mafiin high mention", fit=FitLevel.HIGH),
            ),
            pre_rank=50,
        ),
        make_route(
            index=4,
            project_id="mafiin",
            mentions=(
                make_mention(key="mafiin/low", quote="mafiin low mention", fit=FitLevel.LOW),
            ),
            pre_rank=0,
        ),
    ]
    snapshot = build_snapshot(routes)
    builder = make_queue_builder(snapshot, routes)

    combined = builder.build(project_ids=("midas", "mafiin"))
    midas_only = builder.build(project_ids=("midas",))
    mafiin_only = builder.build(project_ids=("mafiin",))

    assert [project.project_id for project in combined.projects] == ["mafiin", "midas"]
    assert combined.projects[1].candidates == midas_only.projects[0].candidates
    assert combined.projects[0].candidates == mafiin_only.projects[0].candidates


def test_status_filter_hides_other_outcomes() -> None:
    routes = [
        make_route(
            index=1,
            mentions=(make_mention(key="state/accepted", quote="accepted state"),),
        ),
        make_route(
            index=2,
            mentions=(make_mention(key="state/rejected", quote="rejected state"),),
        ),
        make_route(
            index=3,
            mentions=(make_mention(key="state/deferred", quote="deferred state"),),
        ),
        make_route(
            index=4,
            mentions=(make_mention(key="state/inbox", quote="inbox state"),),
        ),
    ]
    decisions = [
        make_decision(key="state/accepted", status=DecisionStatus.ACCEPTED),
        make_decision(key="state/rejected", status=DecisionStatus.REJECTED),
        make_decision(key="state/deferred", status=DecisionStatus.DEFERRED),
    ]
    snapshot = build_snapshot(routes, decisions=decisions)
    builder = make_queue_builder(snapshot, routes, decisions=decisions)

    accepted = builder.build(status=DecisionStatus.ACCEPTED)

    assert candidate_keys(accepted) == ["state/accepted"]
    assert len(candidate_keys(builder.build())) == 4


def test_project_ids_filter_limits_projects() -> None:
    routes = [
        make_route(
            index=1,
            project_id="midas",
            mentions=(make_mention(key="midas/tool", quote="midas tool mention"),),
        ),
        make_route(
            index=2,
            project_id="mafiin",
            mentions=(make_mention(key="mafiin/tool", quote="mafiin tool mention"),),
        ),
    ]
    snapshot = build_snapshot(routes)
    builder = make_queue_builder(snapshot, routes)

    filtered = builder.build(project_ids=("mafiin",))

    assert [project.project_id for project in filtered.projects] == ["mafiin"]


def test_limit_truncates_per_project() -> None:
    routes = [
        make_route(
            index=1,
            project_id="midas",
            mentions=(
                make_mention(key="midas/high", quote="midas high mention", fit=FitLevel.HIGH),
            ),
            pre_rank=5,
        ),
        make_route(
            index=2,
            project_id="midas",
            mentions=(make_mention(key="midas/low", quote="midas low mention", fit=FitLevel.LOW),),
            pre_rank=1,
        ),
        make_route(
            index=3,
            project_id="mafiin",
            mentions=(
                make_mention(key="mafiin/high", quote="mafiin high mention", fit=FitLevel.HIGH),
            ),
            pre_rank=5,
        ),
        make_route(
            index=4,
            project_id="mafiin",
            mentions=(
                make_mention(key="mafiin/low", quote="mafiin low mention", fit=FitLevel.LOW),
            ),
            pre_rank=1,
        ),
    ]
    snapshot = build_snapshot(routes)
    builder = make_queue_builder(snapshot, routes)

    unlimited = builder.build()
    limited = builder.build(limit=1)

    assert all(len(project.candidates) == 1 for project in limited.projects)
    for limited_project, unlimited_project in zip(
        limited.projects, unlimited.projects, strict=True
    ):
        assert limited_project.candidates == (unlimited_project.candidates[0],)


def test_no_snapshot_yields_empty_queue() -> None:
    builder = RegistryQueueBuilder(
        snapshots=FakeSnapshotStore(),
        routes=FakeRouteStore(),
        projects=PROJECTS,
    )

    queue = builder.build()

    assert queue == RegistryQueue(snapshot_id=None, generated_at=None, projects=())


def test_mentions_join_route_fields_and_skip_missing_ids() -> None:
    routes = [
        make_route(
            index=1,
            mentions=(make_mention(key="join/tool", quote="first join quote", fit=FitLevel.HIGH),),
            pre_rank=3,
            source_url="https://example.com/one",
            source_title="First source",
        ),
        make_route(
            index=2,
            mentions=(make_mention(key="join/tool", quote="second join quote", fit=FitLevel.LOW),),
            pre_rank=5,
            source_url="https://example.com/two",
            source_title="Second source",
        ),
    ]
    snapshot = build_snapshot(routes)

    partial = make_queue_builder(snapshot, routes[:1]).build()
    candidate = partial.projects[0].candidates[0]

    assert candidate.mention_count == 2
    assert candidate.score == 3
    assert candidate.mentions == (
        QueueMention(
            source_id=source_id(1),
            capture_id=capture_id(1),
            url="https://example.com/one",
            title="First source",
            quote="first join quote",
            field=AnchorField.DESCRIPTION.value,
            fit=FitLevel.HIGH,
        ),
    )


def test_score_is_max_pre_rank_across_mentions() -> None:
    routes = [
        make_route(
            index=1,
            mentions=(make_mention(key="join/tool", quote="first join quote", fit=FitLevel.HIGH),),
            pre_rank=3,
            source_url="https://example.com/one",
            source_title="First source",
        ),
        make_route(
            index=2,
            mentions=(make_mention(key="join/tool", quote="second join quote", fit=FitLevel.LOW),),
            pre_rank=5,
            source_url="https://example.com/two",
            source_title="Second source",
        ),
    ]
    snapshot = build_snapshot(routes)

    queue = make_queue_builder(snapshot, routes).build()
    candidate = queue.projects[0].candidates[0]

    assert candidate.score == 5
    assert [mention.quote for mention in candidate.mentions] == [
        "first join quote",
        "second join quote",
    ]
