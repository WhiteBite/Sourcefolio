from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import yaml

from sourcefolio.application.project_fit import Project, validate_project_id
from sourcefolio.application.projection_ports import HumanNoteStore

_MAX_PURPOSE = 800
_DEP_NAME = re.compile(r"[A-Za-z0-9._-]+")


class RepoReader(Protocol):
    def read_readme(self, repo_path: str) -> str | None:
        """Return the repository README text, or None when absent or unreadable."""
        ...

    def read_manifest(self, repo_path: str) -> Mapping[str, object] | None:
        """Return the parsed dependency manifest, or None when absent or unreadable."""
        ...


@dataclass(frozen=True)
class ProfileDraftOutcome:
    project_id: str
    created: bool
    error: str | None


@dataclass(frozen=True)
class ProfileDraftResult:
    outcomes: tuple[ProfileDraftOutcome, ...]

    @property
    def created(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.created)

    @property
    def skipped(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.created and outcome.error is None)

    @property
    def created_paths(self) -> tuple[str, ...]:
        return tuple(f"{outcome.project_id}.md" for outcome in self.outcomes if outcome.created)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(outcome.error for outcome in self.outcomes if outcome.error is not None)


def _first_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        stripped = block.strip()
        if not stripped:
            continue
        if all(line.lstrip().startswith("#") for line in stripped.splitlines()):
            continue
        return stripped
    return ""


def _purpose(project: Project, readme: str | None) -> str:
    description = project.description.strip()
    if description:
        return description
    if readme:
        return _first_paragraph(readme)[:_MAX_PURPOSE]
    return ""


def _dependency_names(manifest: Mapping[str, object] | None) -> tuple[str, ...]:
    if manifest is None:
        return ()
    project = manifest.get("project")
    if isinstance(project, Mapping):
        dependencies = project.get("dependencies")
        if isinstance(dependencies, list):
            return tuple(
                name for item in dependencies if isinstance(item, str) and (name := _dep_name(item))
            )
    names: list[str] = []
    for key in ("dependencies", "devDependencies"):
        block = manifest.get(key)
        if isinstance(block, Mapping):
            names.extend(name for name in block if isinstance(name, str))
    return tuple(names)


def _dep_name(spec: str) -> str:
    match = _DEP_NAME.match(spec.strip())
    return match.group(0) if match else ""


def _keywords(manifest: Mapping[str, object] | None) -> tuple[str, ...]:
    if manifest is None:
        return ()
    project = manifest.get("project")
    if isinstance(project, Mapping):
        keywords = project.get("keywords")
        if isinstance(keywords, list):
            return tuple(item for item in keywords if isinstance(item, str))
    keywords = manifest.get("keywords")
    if isinstance(keywords, list):
        return tuple(item for item in keywords if isinstance(item, str))
    return ()


def render_repo_draft(
    project: Project,
    *,
    readme: str | None,
    manifest: Mapping[str, object] | None,
) -> str:
    properties = {
        "id": project.id,
        "name": project.name,
        "status": "draft",
        "type": "project",
    }
    frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {project.name}",
        "",
        "## Purpose",
        "",
        _purpose(project, readme),
        "",
        "## Stack",
        "",
    ]
    dependencies = _dependency_names(manifest)
    if dependencies:
        lines.extend(f"- {name}" for name in dependencies)
    lines.extend(["", "## What's interesting", "", "## Constraints", ""])
    keywords = _keywords(manifest)
    if keywords:
        lines.extend(["## Keywords", "", ", ".join(keywords), ""])
    return "\n".join(lines)


class ProfileDrafter:
    def __init__(self, reader: RepoReader, notes: HumanNoteStore) -> None:
        self._reader = reader
        self._notes = notes

    def draft(self, project: Project) -> ProfileDraftOutcome:
        validate_project_id(project.id)
        if not project.repo_path.strip():
            return ProfileDraftOutcome(
                project_id=project.id,
                created=False,
                error=f"project {project.id} has no repo_path",
            )
        readme = self._reader.read_readme(project.repo_path)
        manifest = self._reader.read_manifest(project.repo_path)
        if readme is None and manifest is None:
            return ProfileDraftOutcome(
                project_id=project.id,
                created=False,
                error=f"repo path not readable: {project.repo_path}",
            )
        created = self._notes.create_if_absent(
            f"{project.id}.md",
            render_repo_draft(project, readme=readme, manifest=manifest),
        )
        return ProfileDraftOutcome(project_id=project.id, created=created, error=None)
