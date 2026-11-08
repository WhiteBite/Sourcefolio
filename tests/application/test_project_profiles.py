from __future__ import annotations

from pathlib import Path

import yaml

from evidence_vault.adapters.filesystem import FileSystemHumanNoteStore
from evidence_vault.application.project_fit import Project
from evidence_vault.application.project_profiles import (
    ProjectProfileScaffolder,
    render_draft,
)

PROJECTS = (
    Project(id="alpha", name="Alpha", description="First project"),
    Project(id="beta", name="Beta", description=""),
)


def test_render_draft_seeds_frontmatter_and_sections() -> None:
    content = render_draft(PROJECTS[0])
    properties = yaml.safe_load(content.split("---", 2)[1])
    assert properties["id"] == "alpha"
    assert properties["name"] == "Alpha"
    assert properties["status"] == "draft"
    assert properties["type"] == "project"
    for marker in ("generated", "generator", "generator_version", "schema_version"):
        assert marker not in properties
    for section in ("## Purpose", "## Stack", "## What's interesting", "## Constraints"):
        assert section in content
    assert "First project" in content


def test_project_profile_scaffolder_creates_then_skips(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    scaffolder = ProjectProfileScaffolder(FileSystemHumanNoteStore(root))

    first = scaffolder.scaffold(PROJECTS)
    assert first.created == 2
    assert first.skipped == 0
    assert first.created_paths == ("alpha.md", "beta.md")
    assert (root / "alpha.md").is_file()
    assert (root / "beta.md").is_file()

    alpha = root / "alpha.md"
    alpha.write_text("human edit", encoding="utf-8")
    second = scaffolder.scaffold(PROJECTS)
    assert second.created == 0
    assert second.skipped == 2
    assert second.created_paths == ()
    assert alpha.read_text(encoding="utf-8") == "human edit"
