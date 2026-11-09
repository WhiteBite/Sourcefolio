from __future__ import annotations

from dataclasses import dataclass

import yaml

from sourcefolio.application.project_fit import Project, validate_project_id
from sourcefolio.application.projection_ports import HumanNoteStore


@dataclass(frozen=True)
class ProfileScaffoldResult:
    created: int
    skipped: int
    created_paths: tuple[str, ...]


def render_draft(project: Project) -> str:
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
        project.description,
        "",
        "## Stack",
        "",
        "## What's interesting",
        "",
        "## Constraints",
        "",
    ]
    return "\n".join(lines)


class ProjectProfileScaffolder:
    def __init__(self, notes: HumanNoteStore) -> None:
        self._notes = notes

    def scaffold(self, projects: tuple[Project, ...]) -> ProfileScaffoldResult:
        created_paths: list[str] = []
        skipped = 0
        for project in projects:
            validate_project_id(project.id)
            path = f"{project.id}.md"
            if self._notes.create_if_absent(path, render_draft(project)):
                created_paths.append(path)
            else:
                skipped += 1
        return ProfileScaffoldResult(
            created=len(created_paths),
            skipped=skipped,
            created_paths=tuple(created_paths),
        )
