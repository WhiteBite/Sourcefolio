from __future__ import annotations

from pathlib import Path

import yaml

from evidence_vault.adapters.filesystem import FileSystemHumanNoteStore
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.application.decisions import (
    DecisionDraft,
    DecisionIndexProjection,
    DecisionScaffolder,
    DecisionSearch,
)


def make_draft(
    *,
    title: str,
    date: str,
    problem: str = "Need resumable jobs.",
    options: tuple[str, ...] = ("SQLite", "JSON files"),
    chosen: str = "SQLite",
    rationale: str = "Transactional resume.",
    follow_ups: tuple[str, ...] = ("Add migrations",),
    projects: tuple[str, ...] = ("evidence-vault",),
    tags: tuple[str, ...] = ("storage",),
) -> DecisionDraft:
    return DecisionDraft(
        title=title,
        problem=problem,
        options=options,
        chosen=chosen,
        rationale=rationale,
        follow_ups=follow_ups,
        projects=projects,
        tags=tags,
        date=date,
    )


def test_scaffold_creates_then_preserves(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    scaffolder = DecisionScaffolder(notes=FileSystemHumanNoteStore(vault / "decisions"))
    draft = make_draft(title="Adopt SQLite control plane", date="2026-11-08")

    assert scaffolder.scaffold(draft) is True
    path = vault / "decisions" / "2026-11-08-adopt-sqlite-control-plane.md"
    assert path.is_file()

    content = path.read_text(encoding="utf-8")
    properties = yaml.safe_load(content.split("---", 2)[1])
    assert properties["date"] == "2026-11-08"
    assert properties["title"] == "Adopt SQLite control plane"
    assert properties["status"] == "open"
    assert properties["type"] == "decision"
    assert properties["projects"] == ["evidence-vault"]
    assert properties["tags"] == ["storage"]
    for section in (
        "## Problem",
        "## Considered options",
        "## Chosen path",
        "## Rationale",
        "## Follow-ups",
    ):
        assert section in content

    path.write_text("human edit", encoding="utf-8")
    assert scaffolder.scaffold(draft) is False
    assert path.read_bytes() == b"human edit"


def test_index_lists_decisions_with_wikilinks(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    scaffolder = DecisionScaffolder(notes=FileSystemHumanNoteStore(vault / "decisions"))
    scaffolder.scaffold(make_draft(title="Adopt SQLite control plane", date="2026-11-08"))
    scaffolder.scaffold(make_draft(title="Drop JSON store", date="2026-11-07"))

    store = FileSystemProjectionStore(vault / "_generated" / "evidence-vault")
    count = DecisionIndexProjection(
        notes=FileSystemHumanNoteStore(vault),
        store=store,
    ).rebuild()

    assert count == 1
    index = (vault / "_generated" / "evidence-vault" / "decisions" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "[[decisions/2026-11-08-adopt-sqlite-control-plane|Adopt SQLite control plane]]" in index
    assert "[[decisions/2026-11-07-drop-json-store|Drop JSON store]]" in index
    assert index.index("2026-11-08") < index.index("2026-11-07")


def test_index_ignores_stray_top_level_note_when_root_is_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    decisions_dir = vault / "decisions"
    decisions_dir.mkdir(parents=True)
    (decisions_dir / "2026-11-08-real.md").write_text(
        "---\ndate: 2026-11-08\ntitle: Real\nstatus: open\n---\n",
        encoding="utf-8",
    )
    (vault / "stray.md").write_text(
        "---\ndate: 2026-11-09\ntitle: Stray\nstatus: open\n---\n",
        encoding="utf-8",
    )

    store = FileSystemProjectionStore(vault / "_generated" / "evidence-vault")
    count = DecisionIndexProjection(
        notes=FileSystemHumanNoteStore(vault),
        store=store,
    ).rebuild()

    assert count == 1
    index = (vault / "_generated" / "evidence-vault" / "decisions" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "Real" in index
    assert "Stray" not in index


def test_search_finds_body_term_and_respects_limit(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    scaffolder = DecisionScaffolder(notes=FileSystemHumanNoteStore(vault / "decisions"))
    scaffolder.scaffold(
        make_draft(title="Adopt SQLite control plane", date="2026-11-08", problem="resumable jobs")
    )
    scaffolder.scaffold(
        make_draft(title="Drop JSON store", date="2026-11-07", problem="resumable backlog")
    )

    search = DecisionSearch(notes=FileSystemHumanNoteStore(vault))

    hits = search.search("resumable", 10)
    assert len(hits) == 2
    assert search.search("resumable", 1) == hits[:1]
    assert search.search("", 10) == ()
