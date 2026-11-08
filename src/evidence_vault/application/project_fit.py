from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue

from evidence_vault.application.extraction_ports import ExtractionRepository
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.llm_enrich import extract_json_object
from evidence_vault.application.llm_ports import ChatClient, LlmCacheStore
from evidence_vault.application.markdown import card_link, escape, generated_frontmatter
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.saved_index import saved_comments, saved_index, saved_str
from evidence_vault.application.slugs import slugify
from evidence_vault.domain.source import CaptureId, SourceId, SourcePacket

SYSTEM_PROMPT = (
    "You match saved TikTok items to the user's own software projects. "
    "Return ONLY one JSON object, no prose and no code fences, with this exact shape: "
    '{"relevant": boolean, "summary": string, '
    '"matches": [{"project_id": string, "fit": "high"|"medium"|"low", '
    '"feature": string, "why": string}]}. '
    "relevant is true only when the item is useful for at least one project. "
    "feature is the concrete function or idea that could be borrowed. "
    "why is a short justification grounded in the item. "
    "project_id must be one of the provided project ids. "
    "If the item is a generic or irrelevant meme, set relevant to false "
    "and matches to an empty array. "
    "Never invent facts that are absent from the item metadata."
)

_MAX_COMMENTS = 10
_FITS = frozenset({"high", "medium", "low"})
_FIT_RANK = {"high": 3, "medium": 2, "low": 1}


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    description: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Project:
        return cls(
            id=_as_str(payload.get("id")),
            name=_as_str(payload.get("name")),
            description=_as_str(payload.get("description")),
        )


def parse_projects(payload: Mapping[str, object]) -> tuple[Project, ...]:
    raw = payload.get("projects")
    if not isinstance(raw, list) or not raw:
        raise ValueError("projects file must contain a non-empty 'projects' array")
    projects: list[Project] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each project must be a JSON object")
        project = Project.from_payload(item)
        if not project.id or not project.name:
            raise ValueError("each project requires a non-empty id and name")
        if project.id in seen:
            raise ValueError(f"duplicate project id: {project.id}")
        seen.add(project.id)
        projects.append(project)
    return tuple(projects)


@dataclass(frozen=True)
class ProjectFitMatch:
    project_id: str
    fit: str
    feature: str
    why: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ProjectFitMatch:
        fit = _as_str(payload.get("fit")).casefold()
        return cls(
            project_id=_as_str(payload.get("project_id")),
            fit=fit if fit in _FITS else "low",
            feature=_as_str(payload.get("feature")),
            why=_as_str(payload.get("why")),
        )

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "project_id": self.project_id,
            "fit": self.fit,
            "feature": self.feature,
            "why": self.why,
        }


def _as_matches(value: object) -> tuple[ProjectFitMatch, ...]:
    if not isinstance(value, list):
        return ()
    result: list[ProjectFitMatch] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        match = ProjectFitMatch.from_payload(item)
        if match.project_id:
            result.append(match)
    return tuple(result)


@dataclass(frozen=True)
class ProjectFitResult:
    relevant: bool
    summary: str
    matches: tuple[ProjectFitMatch, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ProjectFitResult:
        relevant = payload.get("relevant") is True
        return cls(
            relevant=relevant,
            summary=_as_str(payload.get("summary")),
            matches=_as_matches(payload.get("matches")) if relevant else (),
        )

    @classmethod
    def from_text(cls, text: str) -> ProjectFitResult:
        return cls.from_payload(extract_json_object(text))

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "relevant": self.relevant,
            "summary": self.summary,
            "matches": [match.to_payload() for match in self.matches],
        }


@dataclass(frozen=True)
class ProjectFitQuery:
    collections: tuple[str, ...]
    limit: int | None = None
    refresh: bool = False
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.collections or any(not name.strip() for name in self.collections):
            raise ValueError("at least one collection name is required")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")


@dataclass(frozen=True)
class ProjectFitItemResult:
    source_id: SourceId
    capture_id: CaptureId
    status: Literal["processed", "skipped_cached", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class ProjectFitReport:
    collections: tuple[str, ...]
    model: str
    items_scanned: int
    processed: int
    skipped_cached: int
    failed: int
    unmatched: int
    per_project: Mapping[str, int]
    dry_run: bool
    notes: tuple[GeneratedDocument, ...]
    results: tuple[ProjectFitItemResult, ...]


def _projects_block(projects: tuple[Project, ...]) -> str:
    lines = ["Projects:"]
    for project in sorted(projects, key=lambda item: item.id):
        lines.append(f"- {project.id} | {project.name} | {project.description}")
    return "\n".join(lines)


def _build_user_prompt(packet: SourcePacket, projects: tuple[Project, ...], ocr_text: str) -> str:
    saved = saved_index(packet)
    lines = [
        f"URL: {packet.source.canonical_url or 'unknown'}",
        f"Author: {saved_str(saved, 'author') or 'unknown'}",
        f"Description: {saved_str(saved, 'desc') or ''}",
    ]
    comments = saved_comments(saved)
    if comments:
        lines.append("Comments:")
        for comment in comments[:_MAX_COMMENTS]:
            text = saved_str(comment, "text")
            if text:
                lines.append(f"- {text}")
    if ocr_text:
        lines.append("OCR:")
        lines.append(ocr_text)
    lines.append(_projects_block(projects))
    return "\n".join(lines)


def _cache_key(packet: SourcePacket, user_prompt: str) -> str:
    digest = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()[:16]
    return f"{packet.source_id}__{digest}"


def _ocr_text(extractions: ExtractionRepository | None, capture_id: CaptureId) -> str:
    if extractions is None:
        return ""
    parts: list[str] = []
    for record in extractions.iter_for_capture(capture_id):
        text = record.text.strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _valid_matches(
    result: ProjectFitResult,
    known_ids: frozenset[str],
) -> tuple[ProjectFitMatch, ...]:
    return tuple(match for match in result.matches if match.project_id in known_ids)


def _project_slugs(projects: tuple[Project, ...]) -> dict[str, str]:
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for project in projects:
        slug = slugify(project.name)
        if slug in used:
            slug = f"{slug}-{slugify(project.id)}"
        used.add(slug)
        slugs[project.id] = slug
    return slugs


def _render_project_note(
    *,
    project: Project,
    slug: str,
    model: str,
    generated_at: datetime,
    generated_prefix: str,
    entries: list[tuple[SourcePacket, ProjectFitResult]],
    known_ids: frozenset[str],
    item_count: int,
) -> GeneratedDocument:
    matched: list[tuple[SourcePacket, ProjectFitMatch]] = []
    low: list[tuple[SourcePacket, ProjectFitMatch]] = []
    unmatched: list[SourcePacket] = []
    for packet, result in entries:
        match = next((item for item in result.matches if item.project_id == project.id), None)
        if match is not None:
            if match.fit == "low":
                low.append((packet, match))
            else:
                matched.append((packet, match))
        elif _valid_matches(result, known_ids):
            unmatched.append(packet)
    matched.sort(
        key=lambda item: (
            -_FIT_RANK[item[1].fit],
            -item[0].captured_at.timestamp(),
            item[0].capture_id,
        )
    )
    low.sort(key=lambda item: (-item[0].captured_at.timestamp(), item[0].capture_id))
    unmatched.sort(key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id))

    frontmatter = generated_frontmatter(
        {
            "project_id": project.id,
            "project_name": project.name,
            "model": model,
            "match_count": len(matched) + len(low),
            "item_count": item_count,
            "generated_at": generated_at.isoformat(),
            "provenance": "agent_inference",
        },
        schema_version="project-fit/v1",
        generator_version="1",
    )
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {escape(project.name, table=True)} — project fit",
        "",
        "> [!warning] Machine inference",
        "> Derived, rebuildable agent inference, not creator_claim. "
        "Verify against the source cards before relying on it.",
        "",
        f"## Matches ({len(matched)})",
        "",
    ]
    if matched:
        for packet, match in matched:
            title = packet.title or packet.source.external_id or packet.source_id
            lines.append(f"### {escape(title, table=True)}")
            lines.append("")
            lines.append(f"- Source: {card_link(generated_prefix, packet)}")
            lines.append(f"- Fit: {escape(match.fit, table=True)}")
            if match.feature:
                lines.append(f"- Feature: {escape(match.feature, table=True)}")
            if match.why:
                lines.append(f"- Why: {escape(match.why, table=True)}")
            lines.append("")
    else:
        lines.extend(["No matches.", ""])

    lines.extend([f"## Low / undecided ({len(low)})", ""])
    if low:
        for packet, match in low:
            detail = match.feature or match.why
            suffix = f": {escape(detail, table=True)}" if detail else ""
            lines.append(f"- {card_link(generated_prefix, packet)}{suffix}")
    else:
        lines.append("None.")
    lines.append("")

    lines.extend([f"## Unmatched / not relevant ({len(unmatched)})", ""])
    if unmatched:
        lines.extend(f"- {card_link(generated_prefix, packet)}" for packet in unmatched)
    else:
        lines.append("None.")
    lines.append("")

    return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))


class ProjectFitRunner:
    NAMESPACE = "projects"
    CACHE_NAMESPACE = "project-fit"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        client: ChatClient,
        cache: LlmCacheStore,
        model: str,
        projects: tuple[Project, ...],
        extractions: ExtractionRepository | None = None,
        generated_prefix: str = "_generated/evidence-vault",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not projects:
            raise ValueError("at least one project is required")
        self._packets = packets
        self._client = client
        self._cache = cache
        self._model = model
        self._projects = projects
        self._project_ids = frozenset(project.id for project in projects)
        self._extractions = extractions
        self._generated_prefix = generated_prefix.strip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, query: ProjectFitQuery) -> ProjectFitReport:
        selected = self._select(query)
        results: list[ProjectFitItemResult] = []
        entries: list[tuple[SourcePacket, ProjectFitResult]] = []
        for packet in selected:
            ocr_text = _ocr_text(self._extractions, packet.capture_id)
            user_prompt = _build_user_prompt(packet, self._projects, ocr_text)
            key = _cache_key(packet, user_prompt)
            cached = None
            if not query.refresh:
                try:
                    cached = self._cache.load(key)
                except Exception:
                    cached = None
            if cached is not None:
                entries.append((packet, ProjectFitResult.from_payload(cached)))
                results.append(self._result(packet, "skipped_cached"))
                continue
            if query.dry_run:
                results.append(self._result(packet, "processed"))
                continue
            try:
                text = self._client.complete(SYSTEM_PROMPT, user_prompt)
                fit = ProjectFitResult.from_text(text)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                results.append(self._result(packet, "failed", error=message))
                continue
            self._cache.save(key, fit.to_payload())
            entries.append((packet, fit))
            results.append(self._result(packet, "processed"))

        per_project = {project.id: 0 for project in self._projects}
        unmatched = 0
        for _, result in entries:
            valid = _valid_matches(result, self._project_ids)
            if not valid:
                unmatched += 1
                continue
            for match in valid:
                per_project[match.project_id] += 1

        notes: tuple[GeneratedDocument, ...] = ()
        if not query.dry_run:
            slugs = _project_slugs(self._projects)
            notes = tuple(
                _render_project_note(
                    project=project,
                    slug=slugs[project.id],
                    model=self._model,
                    generated_at=self._clock(),
                    generated_prefix=self._generated_prefix,
                    entries=entries,
                    known_ids=self._project_ids,
                    item_count=len(selected),
                )
                for project in self._projects
            )
        return ProjectFitReport(
            collections=query.collections,
            model=self._model,
            items_scanned=len(selected),
            processed=sum(item.status == "processed" for item in results),
            skipped_cached=sum(item.status == "skipped_cached" for item in results),
            failed=sum(item.status == "failed" for item in results),
            unmatched=unmatched,
            per_project=per_project,
            dry_run=query.dry_run,
            notes=notes,
            results=tuple(results),
        )

    def _select(self, query: ProjectFitQuery) -> list[SourcePacket]:
        wanted = {name.casefold() for name in query.collections}
        latest = latest_capture_per_source(self._packets.iter_packets())
        selected = [
            packet
            for packet in latest.values()
            if wanted & {item.name.casefold() for item in packet.collections}
        ]
        selected.sort(key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id))
        if query.limit is not None:
            selected = selected[: query.limit]
        return selected

    @staticmethod
    def _result(
        packet: SourcePacket,
        status: Literal["processed", "skipped_cached", "failed"],
        *,
        error: str | None = None,
    ) -> ProjectFitItemResult:
        return ProjectFitItemResult(
            source_id=packet.source_id,
            capture_id=packet.capture_id,
            status=status,
            error=error,
        )
