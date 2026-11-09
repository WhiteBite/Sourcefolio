from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from evidence_vault.adapters.filesystem import FileSystemHumanNoteStore
from evidence_vault.application.profile_draft import (
    ProfileDrafter,
    ProfileDraftOutcome,
    ProfileDraftResult,
    render_repo_draft,
)
from evidence_vault.application.project_fit import Project

PYPROJECT = {
    "project": {
        "dependencies": ["pydantic>=2", "httpx[socks]==0.27", "anyio"],
        "keywords": ["evidence", "vault"],
    }
}


class FakeRepoReader:
    def __init__(
        self,
        *,
        readme: str | None = None,
        manifest: Mapping[str, object] | None = None,
    ) -> None:
        self._readme = readme
        self._manifest = manifest

    def read_readme(self, repo_path: str) -> str | None:
        return self._readme

    def read_manifest(self, repo_path: str) -> Mapping[str, object] | None:
        return self._manifest


def test_render_repo_draft_seeds_frontmatter_and_sections() -> None:
    project = Project(id="alpha", name="Alpha", description="First project")

    content = render_repo_draft(project, readme=None, manifest=None)

    properties = yaml.safe_load(content.split("---", 2)[1])
    assert properties["id"] == "alpha"
    assert properties["name"] == "Alpha"
    assert properties["status"] == "draft"
    assert properties["type"] == "project"
    for section in ("## Purpose", "## Stack", "## What's interesting", "## Constraints"):
        assert section in content
    assert "First project" in content


def test_render_repo_draft_extracts_stack_and_keywords() -> None:
    project = Project(id="alpha", name="Alpha", description="")

    content = render_repo_draft(project, readme=None, manifest=PYPROJECT)

    assert "- pydantic" in content
    assert "- httpx" in content
    assert "- anyio" in content
    assert "## Keywords" in content
    assert "evidence, vault" in content


def test_render_repo_draft_reads_package_json_dependencies() -> None:
    manifest = {
        "dependencies": {"react": "^18"},
        "devDependencies": {"vitest": "^1"},
    }

    content = render_repo_draft(
        Project(id="alpha", name="Alpha", description=""),
        readme=None,
        manifest=manifest,
    )

    assert "- react" in content
    assert "- vitest" in content


def test_render_repo_draft_uses_readme_paragraph_without_description() -> None:
    readme = "# Alpha\n\nA local evidence archive.\n\nMore detail."

    content = render_repo_draft(
        Project(id="alpha", name="Alpha", description=""),
        readme=readme,
        manifest=None,
    )

    assert "A local evidence archive." in content
    assert "More detail." not in content


def test_render_repo_draft_bounds_readme_paragraph() -> None:
    readme = "x" * 2000

    content = render_repo_draft(
        Project(id="alpha", name="Alpha", description=""),
        readme=readme,
        manifest=None,
    )

    assert "x" * 800 in content
    assert "x" * 801 not in content


def test_profile_drafter_creates_then_skips(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    drafter = ProfileDrafter(
        FakeRepoReader(readme="readme text", manifest=PYPROJECT),
        FileSystemHumanNoteStore(root),
    )
    project = Project(id="alpha", name="Alpha", description="", repo_path="/repo")

    first = drafter.draft(project)
    assert first == ProfileDraftOutcome(project_id="alpha", created=True, error=None)
    assert (root / "alpha.md").is_file()

    (root / "alpha.md").write_text("human edit", encoding="utf-8")
    second = drafter.draft(project)
    assert second == ProfileDraftOutcome(project_id="alpha", created=False, error=None)
    assert (root / "alpha.md").read_text(encoding="utf-8") == "human edit"


def test_profile_drafter_missing_repo_path(tmp_path: Path) -> None:
    drafter = ProfileDrafter(
        FakeRepoReader(readme="readme text"),
        FileSystemHumanNoteStore(tmp_path / "projects"),
    )

    outcome = drafter.draft(Project(id="alpha", name="Alpha", description=""))

    assert outcome.created is False
    assert outcome.error == "project alpha has no repo_path"
    assert not (tmp_path / "projects").exists()


def test_profile_drafter_unreadable_repo(tmp_path: Path) -> None:
    drafter = ProfileDrafter(
        FakeRepoReader(),
        FileSystemHumanNoteStore(tmp_path / "projects"),
    )

    outcome = drafter.draft(Project(id="alpha", name="Alpha", description="", repo_path="/repo"))

    assert outcome.created is False
    assert outcome.error == "repo path not readable: /repo"


def test_profile_drafter_rejects_escaping_id(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    drafter = ProfileDrafter(
        FakeRepoReader(readme="readme text"),
        FileSystemHumanNoteStore(root),
    )

    with pytest.raises(ValueError):
        drafter.draft(Project(id="a/b", name="Escape", description="", repo_path="/repo"))

    assert not (root / "a").exists()
    assert not (tmp_path / "a").exists()


def test_profile_draft_result_aggregates_outcomes() -> None:
    result = ProfileDraftResult(
        outcomes=(
            ProfileDraftOutcome(project_id="alpha", created=True, error=None),
            ProfileDraftOutcome(project_id="beta", created=False, error=None),
            ProfileDraftOutcome(project_id="gamma", created=False, error="boom"),
        )
    )

    assert result.created == 1
    assert result.skipped == 1
    assert result.created_paths == ("alpha.md",)
    assert result.errors == ("boom",)
