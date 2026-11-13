from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemHumanNoteStore,
    FileSystemProjectFitIndex,
    FileSystemRegistryRouteStore,
    FileSystemRegistrySnapshotStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.projections import FileSystemProjectionStore
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.context_brief import ContextBriefBuilder, ContextBriefProjection
from sourcefolio.application.project_fit import Project
from sourcefolio.application.registry_build import RegistryBuilder, RegistryBuildQuery
from sourcefolio.application.registry_queue import RegistryQueueBuilder
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
    RouteVerdict,
)
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

PROJECTS = (
    Project(id="alpha", name="Alpha", description="First project"),
    Project(id="beta", name="Beta", description="Second project"),
)


def add_packet(
    root: Path,
    external_id: str,
    *,
    collection: str = "Нейронки",
    desc: str = "текст",
    captured_at: datetime = NOW,
) -> SourcePacket:
    return (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="tiktok",
                    kind=SourceKind.VIDEO,
                    external_id=external_id,
                    canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
                ),
                captured_at=captured_at,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                collections=(CollectionMembership(name=collection),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption"),
                    ),
                ),
                metadata={"saved_index": {"desc": desc}},
            )
        )
        .packet
    )


def fit_entry(
    packet: SourcePacket,
    *,
    project_id: str = "alpha",
    fit: str = "high",
    updated_at: str = "2026-11-08T00:00:00+00:00",
) -> dict[str, object]:
    return {
        "source_id": packet.source_id,
        "capture_id": packet.capture_id,
        "url": "https://example.test/v",
        "title": "Saved item",
        "relevant": True,
        "summary": "useful",
        "matches": [{"project_id": project_id, "fit": fit, "feature": "f", "why": "w"}],
        "projects_key": "k",
        "updated_at": updated_at,
    }


def save_fit_index(root: Path, entries: list[dict[str, object]]) -> None:
    FileSystemProjectFitIndex(root / "derived").save(
        {"version": 1, "items": {str(entry["source_id"]): entry for entry in entries}}
    )


def make_mention(
    key: str,
    title: str,
    *,
    fit: FitLevel = FitLevel.HIGH,
) -> CandidateMention:
    quote = f"mention of {key}"
    payload: dict[str, object] = {
        "candidate_key": key,
        "kind": CandidateKind.TOOL,
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
    index: int,
    *,
    project_id: str,
    key: str,
    title: str,
    fit: FitLevel = FitLevel.HIGH,
    pre_rank: int = 0,
) -> RegistryRoute:
    identity = {
        "source_id": f"src_{index:064x}",
        "capture_id": f"cap_{index + 100:064x}",
        "project_id": project_id,
        "model": "test-model",
        "prompt_fingerprint": "0123456789abcdef",
    }
    return RegistryRoute(
        route_id=content_id("rt", identity),
        source_id=identity["source_id"],
        capture_id=identity["capture_id"],
        project_id=project_id,
        model=identity["model"],
        prompt_fingerprint=identity["prompt_fingerprint"],
        verdict=RouteVerdict.CANDIDATE,
        mentions=(make_mention(key, title, fit=fit),),
        pre_rank=pre_rank,
        captured_at=NOW,
        provenance=Provenance(actor_kind=ActorKind.AGENT, actor_id="agent", method="registry-test"),
        created_at=NOW,
    )


def make_accepted(project_id: str, key: str, title: str) -> CandidateDecision:
    return CandidateDecision(
        candidate_id=content_id("cand", {"project_id": project_id, "key": key}),
        project_id=project_id,
        key=key,
        kind=CandidateKind.TOOL,
        title=title,
        status=DecisionStatus.ACCEPTED,
        decided_at=NOW,
        provenance=Provenance(actor_kind=ActorKind.USER, actor_id="user", method="candidate-note"),
        approval=Approval(approved_by="user", approved_at=NOW),
    )


def registry_builder(
    root: Path,
    routes: list[RegistryRoute],
    *,
    decisions: tuple[CandidateDecision, ...] = (),
) -> RegistryQueueBuilder:
    derived = root / "derived"
    route_store = FileSystemRegistryRouteStore(derived)
    for route in routes:
        route_store.save(route)
    snapshot_store = FileSystemRegistrySnapshotStore(derived)
    projects = {"alpha": "Alpha", "beta": "Beta", "gamma": "Gamma", "delta": "Delta"}
    reported = RegistryBuilder(
        routes=route_store,
        snapshots=snapshot_store,
        projects=projects,
        decisions=decisions,
        clock=lambda: NOW,
    )
    reported.run(RegistryBuildQuery())
    return RegistryQueueBuilder(
        routes=route_store,
        snapshots=snapshot_store,
        decisions=decisions,
        projects=projects,
    )


def builder(
    root: Path,
    *,
    projects: tuple[Project, ...] = PROJECTS,
    profiles: FileSystemHumanNoteStore | None = None,
    decisions: FileSystemHumanNoteStore | None = None,
    ideas: FileSystemHumanNoteStore | None = None,
    registry: RegistryQueueBuilder | None = None,
) -> ContextBriefBuilder:
    return ContextBriefBuilder(
        packets=FileSystemSourcePacketRepository(root),
        project_fit_index=FileSystemProjectFitIndex(root / "derived"),
        projects=projects,
        profiles=profiles,
        decisions=decisions,
        ideas=ideas,
        clock=lambda: NOW,
        registry=registry,
    )


def test_empty_vault_yields_zero_counts(tmp_path: Path) -> None:
    brief = builder(tmp_path, projects=()).build()

    assert brief.projects == ()
    assert brief.matches == ()
    assert brief.decisions == ()
    assert brief.ideas == ()
    assert brief.stats.sources == 0
    assert brief.stats.collections == 0
    assert brief.stats.ideas == 0
    assert brief.stats.decisions == 0

    payload = json.loads(builder(tmp_path, projects=()).to_json())
    assert payload["stats"] == {"sources": 0, "collections": 0, "ideas": 0, "decisions": 0}


def test_populated_brief_has_projects_matches_decisions(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="trading bot idea")
    save_fit_index(tmp_path, [fit_entry(packet)])
    profiles = FileSystemHumanNoteStore(tmp_path / "profiles")
    profiles.create_if_absent(
        "alpha.md",
        "---\nid: alpha\nname: Alpha\nstatus: draft\ntype: project\n---\n\n"
        "# Alpha\n\nPurpose: trading bot\n",
    )
    decisions = FileSystemHumanNoteStore(tmp_path / "decisions")
    decisions.create_if_absent(
        "2026-11-08-adopt-sqlite.md",
        "---\ndate: 2026-11-08\ntitle: Adopt SQLite\nstatus: open\n"
        "type: decision\nprojects:\n- alpha\n---\n\n# Adopt SQLite\n",
    )
    ideas = FileSystemHumanNoteStore(tmp_path / "ideas")
    ideas.create_if_absent("src_x.md", "---\nstatus: inbox\n---\n")

    brief = builder(tmp_path, profiles=profiles, decisions=decisions, ideas=ideas).build()

    assert len(brief.projects) == 2
    alpha = next(project for project in brief.projects if project.id == "alpha")
    assert alpha.profile is not None
    assert "trading bot" in alpha.profile
    assert "status: draft" not in alpha.profile
    assert "---" not in alpha.profile
    assert len(brief.matches) >= 1
    assert brief.matches[0].project_id == "alpha"
    assert len(brief.decisions) >= 1
    assert brief.decisions[0].title == "Adopt SQLite"
    assert brief.stats.sources == 1
    assert brief.stats.collections == 1
    assert brief.stats.ideas == 1
    assert brief.stats.decisions == 1
    assert isinstance(brief.stats.sources, int)


def test_json_and_markdown_render(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one")
    save_fit_index(tmp_path, [fit_entry(packet)])
    brief_builder = builder(tmp_path)

    payload = json.loads(brief_builder.to_json())
    assert set(payload) == {
        "generated_at",
        "projects",
        "matches",
        "decisions",
        "ideas",
        "stats",
        "candidates",
    }

    markdown = brief_builder.to_markdown()
    assert markdown.startswith("# ")
    for section in ("## Projects", "## Matches", "## Decisions", "## Ideas", "## Stats"):
        assert section in markdown


def test_matches_sorted_top_fit_first(tmp_path: Path) -> None:
    low = add_packet(tmp_path, "one", desc="a")
    high = add_packet(tmp_path, "two", desc="b")
    save_fit_index(
        tmp_path,
        [
            fit_entry(low, project_id="alpha", fit="low", updated_at="2026-11-09T00:00:00+00:00"),
            fit_entry(high, project_id="beta", fit="high", updated_at="2026-11-01T00:00:00+00:00"),
        ],
    )

    brief = builder(tmp_path).build()

    assert [match.fit for match in brief.matches] == ["high", "low"]


def test_decisions_newest_first(tmp_path: Path) -> None:
    decisions = FileSystemHumanNoteStore(tmp_path / "decisions")
    decisions.create_if_absent(
        "2026-11-07-old.md", "---\ndate: 2026-11-07\ntitle: Old\nstatus: open\n---\n"
    )
    decisions.create_if_absent(
        "2026-11-08-new.md", "---\ndate: 2026-11-08\ntitle: New\nstatus: open\n---\n"
    )

    brief = builder(tmp_path, decisions=decisions).build()

    assert [decision.title for decision in brief.decisions] == ["New", "Old"]


def test_build_is_deterministic(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one")
    save_fit_index(tmp_path, [fit_entry(packet)])
    profiles = FileSystemHumanNoteStore(tmp_path / "profiles")
    profiles.create_if_absent("alpha.md", "# Alpha\n")
    decisions = FileSystemHumanNoteStore(tmp_path / "decisions")
    decisions.create_if_absent(
        "2026-11-08-x.md", "---\ndate: 2026-11-08\ntitle: X\nstatus: open\n---\n"
    )
    brief_builder = builder(tmp_path, profiles=profiles, decisions=decisions)

    assert brief_builder.to_json() == brief_builder.to_json()
    assert brief_builder.to_markdown() == brief_builder.to_markdown()


def test_ideas_surfaced_in_markdown_and_json(tmp_path: Path) -> None:
    ideas = FileSystemHumanNoteStore(tmp_path / "ideas")
    ideas.create_if_absent("a.md", "---\ntitle: Alpha idea\nstatus: inbox\n---\n")
    ideas.create_if_absent("b.md", "---\ntitle: Beta idea\nstatus: inbox\n---\n")

    brief_builder = builder(tmp_path, ideas=ideas)
    brief = brief_builder.build()

    assert [idea.title for idea in brief.ideas] == ["Beta idea", "Alpha idea"]
    assert "Beta idea" in brief_builder.to_markdown()
    payload = json.loads(brief_builder.to_json())
    assert payload["ideas"] == [
        {"path": "b.md", "title": "Beta idea"},
        {"path": "a.md", "title": "Alpha idea"},
    ]


def test_per_project_match_cap_keeps_other_projects(tmp_path: Path) -> None:
    entries = [
        fit_entry(add_packet(tmp_path, f"alpha-{index}"), project_id="alpha", fit="high")
        for index in range(5)
    ]
    entries.append(fit_entry(add_packet(tmp_path, "beta-0"), project_id="beta", fit="high"))
    save_fit_index(tmp_path, entries)

    brief = builder(tmp_path).build()

    assert sum(match.project_id == "alpha" for match in brief.matches) == 3
    assert sum(match.project_id == "beta" for match in brief.matches) == 1


def test_context_projection_writes_index_and_json_deterministically(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one")
    save_fit_index(tmp_path, [fit_entry(packet)])
    generated = tmp_path / "vault" / "_generated" / "sourcefolio"
    projection = ContextBriefProjection(FileSystemProjectionStore(generated))

    assert projection.rebuild(builder(tmp_path).build()) == 2
    namespace = generated / ContextBriefProjection.NAMESPACE
    assert sorted(path.name for path in namespace.iterdir() if path.name != "manifest.json") == [
        "context.json",
        "index.md",
    ]
    first = (namespace / "index.md").read_text(encoding="utf-8")
    assert json.loads((namespace / "context.json").read_text(encoding="utf-8"))["generated_at"] == (
        NOW.isoformat()
    )

    projection.rebuild(builder(tmp_path).build())
    assert (namespace / "index.md").read_text(encoding="utf-8") == first


def test_builder_writes_nothing(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one")
    save_fit_index(tmp_path, [fit_entry(packet)])
    profiles = FileSystemHumanNoteStore(tmp_path / "profiles")
    profiles.create_if_absent("alpha.md", "# Alpha\n")
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    builder(tmp_path, profiles=profiles).build()

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after


def test_registry_candidates_are_accepted_first_and_rendered(tmp_path: Path) -> None:
    routes = [
        make_route(1, project_id="alpha", key="tool/accepted", title="Adopted tool"),
        make_route(
            2,
            project_id="alpha",
            key="tool/inbox",
            title="Inbox tool",
            pre_rank=100,
        ),
    ]
    decisions = (make_accepted("alpha", "tool/accepted", "Adopted tool"),)
    registry = registry_builder(tmp_path, routes, decisions=decisions)

    brief_builder = builder(tmp_path, registry=registry)
    brief = brief_builder.build()

    assert [candidate.title for candidate in brief.candidates] == ["Adopted tool", "Inbox tool"]
    assert [candidate.outcome for candidate in brief.candidates] == ["accepted", "inbox"]

    payload = json.loads(brief_builder.to_json())
    assert payload["candidates"][0] == {
        "project_id": "alpha",
        "candidate_id": content_id("cand", {"project_id": "alpha", "key": "tool/accepted"}),
        "title": "Adopted tool",
        "kind": "tool",
        "fit": "high",
        "mention_count": 1,
        "outcome": "accepted",
    }

    markdown = brief_builder.to_markdown()
    assert "## Registry candidates (2)" in markdown
    assert "- [alpha] Adopted tool (tool, high, mentions 1, accepted)" in markdown
    assert "[[_generated/sourcefolio/registry/alpha|registry]]" in markdown


def test_registry_candidates_respect_per_project_and_global_caps(tmp_path: Path) -> None:
    routes = []
    index = 0
    for project_id in ("alpha", "beta", "gamma", "delta"):
        for position in range(4):
            index += 1
            routes.append(
                make_route(
                    index,
                    project_id=project_id,
                    key=f"{project_id}/tool-{position}",
                    title=f"{project_id} tool {position}",
                )
            )
    registry = registry_builder(tmp_path, routes)

    candidates = builder(tmp_path, registry=registry).build().candidates

    assert len(candidates) == 10
    per_project = {
        project_id: sum(candidate.project_id == project_id for candidate in candidates)
        for project_id in ("alpha", "beta", "gamma", "delta")
    }
    assert per_project == {"alpha": 3, "beta": 3, "gamma": 1, "delta": 3}
    assert [candidate.project_id for candidate in candidates] == (
        ["alpha"] * 3 + ["beta"] * 3 + ["delta"] * 3 + ["gamma"]
    )


def test_registry_candidates_keep_project_order_without_global_resort(tmp_path: Path) -> None:
    routes = [
        make_route(
            1,
            project_id="beta",
            key="beta/tool",
            title="Beta tool",
            pre_rank=100,
        ),
        make_route(
            2,
            project_id="alpha",
            key="alpha/tool",
            title="Alpha tool",
            pre_rank=0,
        ),
    ]
    registry = registry_builder(tmp_path, routes)

    candidates = builder(tmp_path, registry=registry).build().candidates

    assert [candidate.project_id for candidate in candidates] == ["alpha", "beta"]


def test_registry_without_snapshot_yields_no_candidates_and_no_section(tmp_path: Path) -> None:
    registry = registry_builder(tmp_path, [])

    brief_builder = builder(tmp_path, registry=registry)
    brief = brief_builder.build()

    assert brief.candidates == ()
    assert "## Registry candidates" not in brief_builder.to_markdown()
    assert json.loads(brief_builder.to_json())["candidates"] == []
