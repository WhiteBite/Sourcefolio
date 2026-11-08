from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from evidence_vault.application.decisions import _frontmatter, _is_decision_note
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.markdown import escape
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.project_fit import (
    _FIT_RANK,
    Project,
    ProjectFitIndexStore,
    _parse_index,
)
from evidence_vault.application.projection_ports import HumanNoteReader

_MAX_MATCHES = 20
_MAX_DECISIONS = 20


@dataclass(frozen=True)
class ContextProject:
    id: str
    name: str
    description: str
    profile: str | None


@dataclass(frozen=True)
class ContextMatch:
    project_id: str
    fit: str
    feature: str
    why: str
    source_id: str
    title: str
    url: str


@dataclass(frozen=True)
class ContextDecision:
    path: str
    title: str
    date: str
    status: str
    projects: tuple[str, ...]


@dataclass(frozen=True)
class ContextStats:
    sources: int
    collections: int
    ideas: int
    decisions: int


@dataclass(frozen=True)
class ContextBrief:
    generated_at: str
    projects: tuple[ContextProject, ...]
    matches: tuple[ContextMatch, ...]
    decisions: tuple[ContextDecision, ...]
    stats: ContextStats


def _profile(profiles: HumanNoteReader | None, project_id: str) -> str | None:
    if profiles is None:
        return None
    try:
        return profiles.read(f"{project_id}.md")
    except Exception:
        return None


def _context_projects(
    projects: tuple[Project, ...],
    profiles: HumanNoteReader | None,
) -> tuple[ContextProject, ...]:
    return tuple(
        ContextProject(
            id=project.id,
            name=project.name,
            description=project.description,
            profile=_profile(profiles, project.id),
        )
        for project in sorted(projects, key=lambda item: item.id)
    )


def _context_matches(index: ProjectFitIndexStore) -> tuple[ContextMatch, ...]:
    try:
        entries = _parse_index(index.load())
    except Exception:
        entries = {}
    ranked: list[tuple[tuple[int, str, str, str], ContextMatch]] = []
    for entry in entries.values():
        for match in entry.matches:
            ranked.append(
                (
                    (
                        _FIT_RANK.get(match.fit, 0),
                        entry.updated_at,
                        entry.source_id,
                        match.project_id,
                    ),
                    ContextMatch(
                        project_id=match.project_id,
                        fit=match.fit,
                        feature=match.feature,
                        why=match.why,
                        source_id=entry.source_id,
                        title=entry.title,
                        url=entry.url,
                    ),
                )
            )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return tuple(match for _, match in ranked[:_MAX_MATCHES])


def _context_decisions(decisions: HumanNoteReader | None) -> tuple[ContextDecision, ...]:
    if decisions is None:
        return ()
    items: list[ContextDecision] = []
    for path, content in decisions.iter_notes():
        if not _is_decision_note(path):
            continue
        properties = _frontmatter(content)
        raw_projects = properties.get("projects")
        projects = (
            tuple(str(item) for item in raw_projects) if isinstance(raw_projects, list) else ()
        )
        items.append(
            ContextDecision(
                path=path,
                title=str(properties.get("title", "")),
                date=str(properties.get("date", "")),
                status=str(properties.get("status", "")),
                projects=projects,
            )
        )
    items.sort(key=lambda item: (item.date, item.path), reverse=True)
    return tuple(items)


def _idea_count(ideas: HumanNoteReader | None) -> int:
    if ideas is None:
        return 0
    return sum(1 for path, _ in ideas.iter_notes() if path.endswith(".md"))


class ContextBriefBuilder:
    def __init__(
        self,
        packets: SourcePacketRepository,
        project_fit_index: ProjectFitIndexStore,
        projects: tuple[Project, ...],
        profiles: HumanNoteReader | None = None,
        decisions: HumanNoteReader | None = None,
        ideas: HumanNoteReader | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._project_fit_index = project_fit_index
        self._projects = projects
        self._profiles = profiles
        self._decisions = decisions
        self._ideas = ideas
        self._clock = clock or (lambda: datetime.now(UTC))

    def build(self) -> ContextBrief:
        latest = latest_capture_per_source(self._packets.iter_packets())
        collections = {
            collection.name for packet in latest.values() for collection in packet.collections
        }
        all_decisions = _context_decisions(self._decisions)
        stats = ContextStats(
            sources=len(latest),
            collections=len(collections),
            ideas=_idea_count(self._ideas),
            decisions=len(all_decisions),
        )
        return ContextBrief(
            generated_at=self._clock().isoformat(),
            projects=_context_projects(self._projects, self._profiles),
            matches=_context_matches(self._project_fit_index),
            decisions=all_decisions[:_MAX_DECISIONS],
            stats=stats,
        )

    def to_json(self) -> str:
        return render_json(self.build())

    def to_markdown(self) -> str:
        return render_markdown(self.build())


def render_json(brief: ContextBrief) -> str:
    return json.dumps(asdict(brief), ensure_ascii=False, indent=2, sort_keys=True)


def render_markdown(brief: ContextBrief) -> str:
    lines = [
        "# Session context brief",
        "",
        f"Generated: {brief.generated_at}",
        "",
        f"## Projects ({len(brief.projects)})",
        "",
    ]
    if brief.projects:
        for project in brief.projects:
            lines.append(
                f"- **{escape(project.name, table=True)}** "
                f"(`{escape(project.id, table=True)}`) — {escape(project.description, table=True)}"
            )
            if project.profile:
                lines.append(f"  - profile: {escape(project.profile, table=True)}")
    else:
        lines.append("None.")
    lines.extend(["", f"## Matches ({len(brief.matches)})", ""])
    if brief.matches:
        for match in brief.matches:
            lines.append(
                f"- [{escape(match.fit, table=True)}] {escape(match.project_id, table=True)} — "
                f"{escape(match.feature, table=True)} — {escape(match.why, table=True)} "
                f"({escape(match.source_id, table=True)})"
            )
    else:
        lines.append("None.")
    lines.extend(["", f"## Decisions ({len(brief.decisions)})", ""])
    if brief.decisions:
        for decision in brief.decisions:
            lines.append(
                f"- {escape(decision.date, table=True)} — "
                f"{escape(decision.title, table=True)} ({escape(decision.status, table=True)})"
            )
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "## Stats",
            "",
            f"- Sources: {brief.stats.sources}",
            f"- Collections: {brief.stats.collections}",
            f"- Ideas: {brief.stats.ideas}",
            f"- Decisions: {brief.stats.decisions}",
            "",
        ]
    )
    return "\n".join(lines)
