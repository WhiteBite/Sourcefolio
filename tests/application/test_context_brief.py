from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemHumanNoteStore,
    FileSystemProjectFitIndex,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.context_brief import ContextBriefBuilder, ContextBriefProjection
from evidence_vault.application.project_fit import Project
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
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


def builder(
    root: Path,
    *,
    projects: tuple[Project, ...] = PROJECTS,
    profiles: FileSystemHumanNoteStore | None = None,
    decisions: FileSystemHumanNoteStore | None = None,
    ideas: FileSystemHumanNoteStore | None = None,
) -> ContextBriefBuilder:
    return ContextBriefBuilder(
        packets=FileSystemSourcePacketRepository(root),
        project_fit_index=FileSystemProjectFitIndex(root / "derived"),
        projects=projects,
        profiles=profiles,
        decisions=decisions,
        ideas=ideas,
        clock=lambda: NOW,
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
    assert set(payload) == {"generated_at", "projects", "matches", "decisions", "ideas", "stats"}

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
    generated = tmp_path / "vault" / "_generated" / "evidence-vault"
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
