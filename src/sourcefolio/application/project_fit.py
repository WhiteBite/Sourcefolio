from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import JsonValue

from sourcefolio.application.extraction_ports import ExtractionRepository
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.llm_enrich import extract_json_object
from sourcefolio.application.llm_ports import ChatClient, LlmCacheStore
from sourcefolio.application.markdown import (
    card_link_ids,
    escape,
    generated_frontmatter,
    wikilink_alias,
)
from sourcefolio.application.ports import SourcePacketRepository
from sourcefolio.application.projection_types import GeneratedDocument
from sourcefolio.application.saved_index import saved_comments, saved_index, saved_str
from sourcefolio.application.slugs import slugify
from sourcefolio.domain.source import CaptureId, SourceId, SourcePacket

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
FIT_RANK = {"high": 3, "medium": 2, "low": 1}
_MAX_ATTEMPTS = 3
_DRY_RUN_PREVIEWS = 2
_MAX_IRRELEVANT = 20
_INDEX_VERSION = 1
_GENERATOR_VERSION = "1"
PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    description: str
    profile: str = ""
    repo_path: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Project:
        return cls(
            id=_as_str(payload.get("id")),
            name=_as_str(payload.get("name")),
            description=_as_str(payload.get("description")),
            profile=_as_str(payload.get("profile")),
            repo_path=_as_str(payload.get("repo_path")),
        )


def validate_project_id(value: str) -> str:
    if not PROJECT_ID_PATTERN.fullmatch(value):
        raise ValueError(f"invalid project id: {value!r}")
    return value


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
        validate_project_id(project.id)
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
class ProjectFitIndexEntry:
    source_id: str
    capture_id: str
    url: str
    title: str
    relevant: bool
    summary: str
    matches: tuple[ProjectFitMatch, ...]
    projects_key: str
    updated_at: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ProjectFitIndexEntry:
        return cls(
            source_id=_as_str(payload.get("source_id")),
            capture_id=_as_str(payload.get("capture_id")),
            url=_as_str(payload.get("url")),
            title=_as_str(payload.get("title")),
            relevant=payload.get("relevant") is True,
            summary=_as_str(payload.get("summary")),
            matches=_as_matches(payload.get("matches")),
            projects_key=_as_str(payload.get("projects_key")),
            updated_at=_as_str(payload.get("updated_at")),
        )

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "source_id": self.source_id,
            "capture_id": self.capture_id,
            "url": self.url,
            "title": self.title,
            "relevant": self.relevant,
            "summary": self.summary,
            "matches": [match.to_payload() for match in self.matches],
            "projects_key": self.projects_key,
            "updated_at": self.updated_at,
        }

    def same_content(self, other: ProjectFitIndexEntry) -> bool:
        return (
            self.source_id == other.source_id
            and self.capture_id == other.capture_id
            and self.url == other.url
            and self.title == other.title
            and self.relevant == other.relevant
            and self.summary == other.summary
            and self.matches == other.matches
            and self.projects_key == other.projects_key
        )


class ProjectFitIndexStore(Protocol):
    def load(self) -> Mapping[str, JsonValue] | None:
        """Return the persisted match index, or None when absent or unreadable."""
        ...

    def save(self, payload: Mapping[str, JsonValue]) -> None:
        """Persist the match index, replacing any previous version."""
        ...


def parse_index(payload: Mapping[str, JsonValue] | None) -> dict[str, ProjectFitIndexEntry]:
    if not isinstance(payload, Mapping):
        return {}
    items = payload.get("items")
    if not isinstance(items, dict):
        return {}
    entries: dict[str, ProjectFitIndexEntry] = {}
    for value in items.values():
        if not isinstance(value, dict):
            continue
        entry = ProjectFitIndexEntry.from_payload(value)
        if entry.source_id:
            entries[entry.source_id] = entry
    return entries


def index_project_slugs(payload: Mapping[str, JsonValue] | None) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        return {}
    raw = payload.get("project_slugs")
    if not isinstance(raw, dict):
        return {}
    return {
        key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)
    }


def _index_payload(
    entries: Mapping[str, ProjectFitIndexEntry],
    slugs: Mapping[str, str],
) -> dict[str, JsonValue]:
    return {
        "version": _INDEX_VERSION,
        "project_slugs": dict(slugs),
        "items": {key: entries[key].to_payload() for key in sorted(entries)},
    }


@dataclass(frozen=True)
class ProjectFitQuery:
    collections: tuple[str, ...] = ()
    limit: int | None = None
    refresh: bool = False
    dry_run: bool = False
    max_ocr_kb: int = 20

    def __post_init__(self) -> None:
        if any(not name.strip() for name in self.collections):
            raise ValueError("collection names must be non-empty")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.max_ocr_kb < 1:
            raise ValueError("max_ocr_kb must be at least 1")


@dataclass(frozen=True)
class ProjectFitItemResult:
    source_id: SourceId
    capture_id: CaptureId
    status: Literal["processed", "skipped_cached", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class ProjectFitReport:
    collections: tuple[str, ...]
    all_collections: bool
    model: str
    items_scanned: int
    processed: int
    skipped_cached: int
    failed: int
    unmatched: int
    per_project: Mapping[str, int]
    dry_run: bool
    notes: tuple[GeneratedDocument, ...]
    previews: tuple[tuple[str, str], ...]
    results: tuple[ProjectFitItemResult, ...]


def _projects_block(projects: tuple[Project, ...]) -> str:
    lines = ["Projects:"]
    for project in sorted(projects, key=lambda item: item.id):
        lines.append(f"- {project.id} | {project.name} | {project.description}")
        profile = project.profile.strip()
        if profile:
            lines.append(f"  Profile: {profile}")
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


def _cache_key(packet: SourcePacket, user_prompt: str, *, model: str) -> str:
    material = f"{_GENERATOR_VERSION}\x00{model}\x00{user_prompt}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{packet.source_id}__{digest}"


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _ocr_text(
    extractions: ExtractionRepository | None,
    capture_id: CaptureId,
    max_ocr_kb: int,
) -> str:
    if extractions is None:
        return ""
    parts: list[str] = []
    for record in extractions.iter_for_capture(capture_id):
        text = record.text.strip()
        if text:
            parts.append(text)
    return _truncate_utf8("\n".join(parts), max_ocr_kb * 1024)


def _valid_matches(
    result: ProjectFitResult,
    known_ids: frozenset[str],
) -> tuple[ProjectFitMatch, ...]:
    return tuple(match for match in result.matches if match.project_id in known_ids)


def project_slugs(projects: tuple[Project, ...]) -> dict[str, str]:
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for project in projects:
        slug = slugify(project.name)
        if slug in used:
            slug = f"{slug}-{slugify(project.id)}"
        used.add(slug)
        slugs[project.id] = slug
    return slugs


def entry_title(entry: ProjectFitIndexEntry) -> str:
    if entry.summary:
        first_line = entry.summary.splitlines()[0].strip()
        if first_line:
            return first_line
    return entry.title or entry.source_id


def _entry_link(generated_prefix: str, entry: ProjectFitIndexEntry) -> str:
    return card_link_ids(
        generated_prefix,
        entry.source_id,
        entry.capture_id,
        entry.title or entry.source_id,
    )


def _render_project_note(
    *,
    project: Project,
    slug: str,
    model: str,
    generated_at: datetime,
    generated_prefix: str,
    entries: list[ProjectFitIndexEntry],
    known_ids: frozenset[str],
    item_count: int,
) -> GeneratedDocument:
    matched: list[tuple[ProjectFitIndexEntry, ProjectFitMatch]] = []
    low: list[tuple[ProjectFitIndexEntry, ProjectFitMatch]] = []
    mentioned: list[ProjectFitIndexEntry] = []
    for entry in entries:
        match = next((item for item in entry.matches if item.project_id == project.id), None)
        if match is not None:
            if match.fit == "low":
                low.append((entry, match))
            else:
                matched.append((entry, match))
        elif any(item.project_id in known_ids for item in entry.matches):
            mentioned.append(entry)
    matched.sort(
        key=lambda item: (FIT_RANK[item[1].fit], item[0].updated_at, item[0].source_id),
        reverse=True,
    )
    low.sort(key=lambda item: (item[0].updated_at, item[0].source_id), reverse=True)
    mentioned.sort(key=lambda entry: (entry.updated_at, entry.source_id), reverse=True)

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
        f"## Matches for {escape(project.name, table=True)} ({len(matched)})",
        "",
    ]
    if matched:
        for entry, match in matched:
            lines.append(f"### {escape(entry_title(entry), table=True)}")
            lines.append("")
            lines.append(f"- Source: {_entry_link(generated_prefix, entry)}")
            lines.append(f"- Fit: {escape(match.fit, table=True)}")
            if match.feature:
                lines.append(f"- Feature: {escape(match.feature, table=True)}")
            if match.why:
                lines.append(f"- Why: {escape(match.why, table=True)}")
            if entry.summary:
                lines.extend(["", escape(entry.summary, table=True)])
            lines.append("")
    else:
        lines.extend(["No matches.", ""])

    lines.extend([f"## Low / undecided ({len(low)})", ""])
    if low:
        for entry, match in low:
            detail = match.feature or match.why
            suffix = f": {escape(detail, table=True)}" if detail else ""
            lines.append(f"- {_entry_link(generated_prefix, entry)}{suffix}")
    else:
        lines.append("None.")
    lines.append("")

    lines.extend(
        [f"## Mentioned but not for {escape(project.name, table=True)} ({len(mentioned)})", ""]
    )
    if mentioned:
        lines.extend(f"- {_entry_link(generated_prefix, entry)}" for entry in mentioned)
    else:
        lines.append("None.")
    lines.append("")

    return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))


def _render_index(
    *,
    projects: tuple[Project, ...],
    slugs: dict[str, str],
    model: str,
    generated_at: datetime,
    generated_prefix: str,
    entries: list[ProjectFitIndexEntry],
    known_ids: frozenset[str],
) -> GeneratedDocument:
    counts = {project.id: 0 for project in projects}
    irrelevant: list[ProjectFitIndexEntry] = []
    for entry in entries:
        valid = tuple(match for match in entry.matches if match.project_id in known_ids)
        if not valid:
            irrelevant.append(entry)
            continue
        for match in valid:
            counts[match.project_id] += 1
    irrelevant.sort(key=lambda entry: (entry.updated_at, entry.source_id), reverse=True)

    frontmatter = generated_frontmatter(
        {
            "project_count": len(projects),
            "item_count": len(entries),
            "irrelevant_count": len(irrelevant),
            "model": model,
            "generated_at": generated_at.isoformat(),
            "provenance": "agent_inference",
        },
        schema_version="project-fit-index/v1",
        generator_version="1",
    )
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        "# Project fit",
        "",
        "> [!warning] Machine inference",
        "> Derived, rebuildable agent inference, not creator_claim. "
        "Verify against the source cards before relying on it.",
        "",
        "## Projects",
        "",
        "| Project | Matches | Note |",
        "|---|---:|---|",
    ]
    for project in sorted(projects, key=lambda item: item.id):
        target = f"{generated_prefix}/projects/{slugs[project.id]}"
        lines.append(
            f"| {escape(project.name, table=True)} | {counts[project.id]} "
            f"| [[{target}|{wikilink_alias(project.name)}]] |"
        )
    lines.append("")
    lines.extend([f"## Globally irrelevant ({len(irrelevant)})", ""])
    if irrelevant:
        lines.extend(
            f"- {_entry_link(generated_prefix, entry)}" for entry in irrelevant[:_MAX_IRRELEVANT]
        )
    else:
        lines.append("None.")
    lines.append("")
    return GeneratedDocument(path="index.md", content="\n".join(lines))


class ProjectFitRunner:
    NAMESPACE = "projects"
    CACHE_NAMESPACE = "project-fit"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        client: ChatClient,
        cache: LlmCacheStore,
        index: ProjectFitIndexStore,
        model: str,
        projects: tuple[Project, ...],
        extractions: ExtractionRepository | None = None,
        generated_prefix: str = "_generated/sourcefolio",
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        retry_backoff_seconds: float = 0.5,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        if not projects:
            raise ValueError("at least one project is required")
        self._packets = packets
        self._client = client
        self._cache = cache
        self._index = index
        self._model = model
        self._projects = projects
        self._project_ids = frozenset(project.id for project in projects)
        self._projects_key = _projects_key(projects)
        self._extractions = extractions
        self._generated_prefix = generated_prefix.strip("/")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._retry_backoff = retry_backoff_seconds
        self._progress = progress

    def run(self, query: ProjectFitQuery) -> ProjectFitReport:
        selected = self._select(query)
        existing = self._load_index()
        entries = dict(existing)
        results: list[ProjectFitItemResult] = []
        previews: list[tuple[str, str]] = []
        total = len(selected)
        for position, packet in enumerate(selected, start=1):
            ocr_text = _ocr_text(self._extractions, packet.capture_id, query.max_ocr_kb)
            user_prompt = _build_user_prompt(packet, self._projects, ocr_text)
            key = _cache_key(packet, user_prompt, model=self._model)
            cached = None
            if not query.refresh:
                try:
                    cached = self._cache.load(key)
                except Exception:
                    cached = None
            if cached is not None:
                result = ProjectFitResult.from_payload(cached)
                entries[packet.source_id] = self._entry(
                    packet, result, existing.get(packet.source_id)
                )
                results.append(self._result(packet, "skipped_cached"))
                self._emit_progress(position, total, "skipped")
                continue
            if query.dry_run:
                if len(previews) < _DRY_RUN_PREVIEWS:
                    previews.append((SYSTEM_PROMPT, user_prompt))
                results.append(self._result(packet, "processed"))
                self._emit_progress(position, total, "processed")
                continue
            fit, error = self._complete(user_prompt)
            if fit is None:
                results.append(self._result(packet, "failed", error=error))
                self._emit_progress(position, total, "failed")
                continue
            self._cache.save(key, fit.to_payload())
            entries[packet.source_id] = self._entry(packet, fit, existing.get(packet.source_id))
            results.append(self._result(packet, "processed"))
            self._emit_progress(position, total, "processed")

        if not query.dry_run:
            self._index.save(_index_payload(entries, project_slugs(self._projects)))

        per_project, unmatched = self._aggregate(entries)
        notes: tuple[GeneratedDocument, ...] = ()
        if not query.dry_run:
            notes = self._render(entries)
        return ProjectFitReport(
            collections=query.collections,
            all_collections=not query.collections,
            model=self._model,
            items_scanned=len(selected),
            processed=sum(item.status == "processed" for item in results),
            skipped_cached=sum(item.status == "skipped_cached" for item in results),
            failed=sum(item.status == "failed" for item in results),
            unmatched=unmatched,
            per_project=per_project,
            dry_run=query.dry_run,
            notes=notes,
            previews=tuple(previews),
            results=tuple(results),
        )

    def _complete(self, user_prompt: str) -> tuple[ProjectFitResult | None, str | None]:
        error: str | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                text = self._client.complete(SYSTEM_PROMPT, user_prompt)
                return ProjectFitResult.from_text(text), None
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                if attempt + 1 < _MAX_ATTEMPTS:
                    self._sleep(self._retry_backoff * (attempt + 1))
        return None, error

    def _entry(
        self,
        packet: SourcePacket,
        result: ProjectFitResult,
        previous: ProjectFitIndexEntry | None,
    ) -> ProjectFitIndexEntry:
        entry = ProjectFitIndexEntry(
            source_id=packet.source_id,
            capture_id=packet.capture_id,
            url=packet.source.canonical_url or "",
            title=packet.title or packet.source.external_id or "",
            relevant=result.relevant,
            summary=result.summary,
            matches=_valid_matches(result, self._project_ids),
            projects_key=self._projects_key,
            updated_at=self._clock().isoformat(),
        )
        if previous is not None and previous.same_content(entry):
            return previous
        return entry

    def _aggregate(
        self,
        entries: Mapping[str, ProjectFitIndexEntry],
    ) -> tuple[dict[str, int], int]:
        per_project = {project.id: 0 for project in self._projects}
        unmatched = 0
        for entry in entries.values():
            valid = tuple(match for match in entry.matches if match.project_id in self._project_ids)
            if not valid:
                unmatched += 1
                continue
            for match in valid:
                per_project[match.project_id] += 1
        return per_project, unmatched

    def _render(
        self,
        entries: Mapping[str, ProjectFitIndexEntry],
    ) -> tuple[GeneratedDocument, ...]:
        ordered = list(entries.values())
        slugs = project_slugs(self._projects)
        generated_at = self._clock()
        documents = [
            _render_project_note(
                project=project,
                slug=slugs[project.id],
                model=self._model,
                generated_at=generated_at,
                generated_prefix=self._generated_prefix,
                entries=ordered,
                known_ids=self._project_ids,
                item_count=len(ordered),
            )
            for project in self._projects
        ]
        documents.append(
            _render_index(
                projects=self._projects,
                slugs=slugs,
                model=self._model,
                generated_at=generated_at,
                generated_prefix=self._generated_prefix,
                entries=ordered,
                known_ids=self._project_ids,
            )
        )
        return tuple(documents)

    def _load_index(self) -> dict[str, ProjectFitIndexEntry]:
        try:
            payload = self._index.load()
        except Exception:
            return {}
        return parse_index(payload)

    def _emit_progress(self, position: int, total: int, status: str) -> None:
        if self._progress is not None:
            self._progress(position, total, status)

    def _select(self, query: ProjectFitQuery) -> list[SourcePacket]:
        latest = latest_capture_per_source(self._packets.iter_packets())
        if query.collections:
            wanted = {name.casefold() for name in query.collections}
            selected = [
                packet
                for packet in latest.values()
                if wanted & {item.name.casefold() for item in packet.collections}
            ]
        else:
            selected = list(latest.values())
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


def _projects_key(projects: tuple[Project, ...]) -> str:
    payload = [
        {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "profile": project.profile,
        }
        for project in projects
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]
