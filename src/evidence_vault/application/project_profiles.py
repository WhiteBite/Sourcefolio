from __future__ import annotations

from dataclasses import dataclass

from evidence_vault.application.markdown import generated_frontmatter
from evidence_vault.application.project_fit import Project
from evidence_vault.application.projection_ports import HumanNoteStore


@dataclass(frozen=True)
class ProfileScaffoldResult:
    created: int
    skipped: int
    created_paths: tuple[str, ...]


def render_draft(project: Project) -> str:
    frontmatter = generated_frontmatter(
        {
            "id": project.id,
            "name": project.name,
            "status": "draft",
            "type": "project",
        },
        schema_version="project-profile/v1",
        generator_version="1",
    )
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
