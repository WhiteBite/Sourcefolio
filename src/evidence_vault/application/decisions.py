from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from evidence_vault.application.markdown import (
    escape,
    generated_callout,
    generated_frontmatter,
    wikilink_alias,
)
from evidence_vault.application.projection_ports import (
    HumanNoteReader,
    HumanNoteStore,
    ProjectionStore,
)
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.slugs import slugify

_HUMAN_DIR = "decisions"
_TERM = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class DecisionDraft:
    title: str
    problem: str
    options: tuple[str, ...]
    chosen: str
    rationale: str
    follow_ups: tuple[str, ...]
    projects: tuple[str, ...]
    tags: tuple[str, ...]
    date: str


@dataclass(frozen=True)
class DecisionHit:
    path: str
    title: str
    date: str
    status: str


def _frontmatter(content: str) -> dict[str, object]:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    loaded = yaml.safe_load(parts[1])
    if not isinstance(loaded, dict):
        return {}
    return {str(key): value for key, value in loaded.items()}


def _is_decision_note(path: str, root: Path) -> bool:
    if not path.endswith(".md"):
        return False
    if path.startswith(f"{_HUMAN_DIR}/"):
        return True
    return root.name == _HUMAN_DIR and "/" not in path


def _wikilink_target(path: str) -> str:
    return f"{_HUMAN_DIR}/{PurePosixPath(path).stem}"


class DecisionScaffolder:
    def __init__(self, notes: HumanNoteStore) -> None:
        self._notes = notes

    @staticmethod
    def path_for(draft: DecisionDraft) -> str:
        return f"{draft.date}-{slugify(draft.title)}.md"

    def scaffold(self, draft: DecisionDraft) -> bool:
        return self._notes.create_if_absent(self.path_for(draft), self._render(draft))

    @staticmethod
    def _render(draft: DecisionDraft) -> str:
        properties = {
            "date": draft.date,
            "title": draft.title,
            "status": "open",
            "type": "decision",
            "projects": list(draft.projects),
            "tags": list(draft.tags),
        }
        frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {draft.title}",
            "",
            "## Problem",
            "",
            draft.problem,
            "",
            "## Considered options",
            "",
            *(f"- {option}" for option in draft.options),
            "",
            "## Chosen path",
            "",
            draft.chosen,
            "",
            "## Rationale",
            "",
            draft.rationale,
            "",
            "## Follow-ups",
            "",
            *(f"- {item}" for item in draft.follow_ups),
            "",
        ]
        return "\n".join(lines)


class DecisionIndexProjection:
    NAMESPACE = "decisions"

    def __init__(self, notes: HumanNoteReader, store: ProjectionStore) -> None:
        self._notes = notes
        self._store = store

    def rebuild(self) -> int:
        entries = [self._entry(path, content) for path, content in self._decisions()]
        entries.sort(key=lambda entry: entry[0], reverse=True)
        return self._store.replace_namespace(self.NAMESPACE, (self._render(entries),))

    def _decisions(self) -> Iterator[tuple[str, str]]:
        for path, content in self._notes.iter_notes():
            if _is_decision_note(path, self._notes.root):
                yield path, content

    @staticmethod
    def _entry(path: str, content: str) -> tuple[str, str, str, str, str]:
        properties = _frontmatter(content)
        projects = properties.get("projects")
        projects_text = (
            ", ".join(str(item) for item in projects) if isinstance(projects, list) else ""
        )
        return (
            str(properties.get("date", "")),
            str(properties.get("title", "")),
            str(properties.get("status", "")),
            projects_text,
            path,
        )

    def _render(self, entries: list[tuple[str, str, str, str, str]]) -> GeneratedDocument:
        frontmatter = generated_frontmatter(
            {"decision_count": len(entries)},
            schema_version="decision-index/v1",
            generator_version="1",
        )
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            "# Decisions",
            "",
            *generated_callout(
                "Rebuildable from human decision notes. Edit notes in `decisions/`."
            ),
            "",
            "| Date | Title | Status | Projects |",
            "|---|---|---|---|",
        ]
        for date, title, status, projects_text, path in entries:
            target = _wikilink_target(path)
            lines.append(
                f"| {escape(date, table=True)} | [[{target}|{wikilink_alias(title)}]] "
                f"| {escape(status, table=True)} | {escape(projects_text, table=True)} |"
            )
        lines.append("")
        return GeneratedDocument(path="index.md", content="\n".join(lines))


class DecisionSearch:
    def __init__(self, notes: HumanNoteReader) -> None:
        self._notes = notes

    def search(self, query: str, limit: int) -> tuple[DecisionHit, ...]:
        terms = [term.casefold() for term in _TERM.findall(query)]
        if not terms or limit < 1:
            return ()
        hits: list[DecisionHit] = []
        for path, content in self._notes.iter_notes():
            if not _is_decision_note(path, self._notes.root):
                continue
            folded = content.casefold()
            if any(term not in folded for term in terms):
                continue
            properties = _frontmatter(content)
            hits.append(
                DecisionHit(
                    path=path,
                    title=str(properties.get("title", "")),
                    date=str(properties.get("date", "")),
                    status=str(properties.get("status", "")),
                )
            )
        return tuple(hits[:limit])
