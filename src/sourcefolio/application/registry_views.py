from __future__ import annotations

from collections.abc import Iterable

from sourcefolio.application.markdown import (
    card_link_ids,
    escape,
    generated_frontmatter,
)
from sourcefolio.application.projection_types import GeneratedDocument
from sourcefolio.application.registry_queue import (
    ProjectQueue,
    QueueCandidate,
    QueueMention,
    RegistryQueue,
)
from sourcefolio.application.slugs import unique_slugs
from sourcefolio.domain.registry import DecisionStatus

_MAX_QUOTES = 3


def _inference_callout() -> list[str]:
    return [
        "> [!warning] Machine inference",
        "> Candidates are agent inference from captured sources, "
        "not creator claims or verified facts.",
    ]


def _accepted_count(candidates: Iterable[QueueCandidate]) -> int:
    accepted = DecisionStatus.ACCEPTED.value
    return sum(1 for candidate in candidates if candidate.outcome == accepted)


def _project_slugs(projects: Iterable[ProjectQueue]) -> dict[str, str]:
    items = tuple(projects)
    by_name = unique_slugs(project.name for project in items)
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for project in items:
        slug = by_name[project.name]
        if slug in used:
            slug = project.project_id
        used.add(slug)
        slugs[project.project_id] = slug
    return slugs


def _quote_lines(mention: QueueMention, generated_prefix: str, alias: str) -> list[str]:
    lines = [f"> {line}" for line in mention.quote.splitlines() or [""]]
    lines.extend(
        [
            ">",
            f"> {card_link_ids(generated_prefix, mention.source_id, mention.capture_id, alias)}",
            "",
        ]
    )
    return lines


def _candidate_lines(candidate: QueueCandidate, generated_prefix: str) -> list[str]:
    lines = [
        f"### {escape(candidate.title)}",
        "",
        f"- Kind: {candidate.kind.value}",
        f"- Fit: {candidate.fit.value}",
        f"- Mentions: {candidate.mention_count}",
        f"- Outcome: **{candidate.outcome}**",
        "",
    ]
    for mention in candidate.mentions[:_MAX_QUOTES]:
        alias = mention.title or candidate.title
        lines.extend(_quote_lines(mention, generated_prefix, alias))
    return lines


class RegistryViewProjection:
    NAMESPACE = "registry"

    def __init__(self, *, generated_prefix: str = "sourcefolio") -> None:
        self._generated_prefix = generated_prefix.strip("/")

    def render(self, queue: RegistryQueue) -> tuple[GeneratedDocument, ...]:
        slugs = _project_slugs(queue.projects)
        documents = [self._index(queue, slugs)]
        documents.extend(
            self._project(project, slugs[project.project_id], queue) for project in queue.projects
        )
        return tuple(documents)

    def _frontmatter(self, extra: dict[str, object]) -> str:
        return generated_frontmatter(
            extra,
            schema_version="registry-view/v1",
            generator_version="1",
        )

    def _index(
        self,
        queue: RegistryQueue,
        slugs: dict[str, str],
    ) -> GeneratedDocument:
        candidates = [candidate for project in queue.projects for candidate in project.candidates]
        extra: dict[str, object] = {"provenance": "agent_inference"}
        if queue.generated_at is not None:
            extra["generated_at"] = queue.generated_at
        extra.update(
            {
                "snapshot_id": queue.snapshot_id,
                "project_count": len(queue.projects),
                "candidate_count": len(candidates),
                "accepted_count": _accepted_count(candidates),
            }
        )
        lines = [
            "---",
            self._frontmatter(extra),
            "---",
            "",
            "# Registry queue",
            "",
            *_inference_callout(),
            "",
            "## Projects",
            "",
        ]
        if not queue.projects:
            lines.append("No projects in the queue.")
        for project in queue.projects:
            slug = slugs[project.project_id]
            lines.append(
                f"- [{escape(project.name)}]({slug}.md): "
                f"{len(project.candidates)} candidates, "
                f"{_accepted_count(project.candidates)} accepted"
            )
        lines.append("")
        return GeneratedDocument(path="index.md", content="\n".join(lines))

    def _project(
        self,
        project: ProjectQueue,
        slug: str,
        queue: RegistryQueue,
    ) -> GeneratedDocument:
        extra: dict[str, object] = {"provenance": "agent_inference"}
        if queue.generated_at is not None:
            extra["generated_at"] = queue.generated_at
        extra.update(
            {
                "snapshot_id": queue.snapshot_id,
                "project_id": project.project_id,
                "project_name": project.name,
                "candidate_count": len(project.candidates),
                "accepted_count": _accepted_count(project.candidates),
            }
        )
        lines = [
            "---",
            self._frontmatter(extra),
            "---",
            "",
            f"# {escape(project.name)}",
            "",
            *_inference_callout(),
            "",
        ]
        if not project.candidates:
            lines.append("No candidates for this project.")
        for candidate in project.candidates:
            lines.extend(_candidate_lines(candidate, self._generated_prefix))
        lines.append("")
        return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))
