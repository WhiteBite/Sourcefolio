from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue

from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.llm_enrich import _build_user_prompt, extract_json_object
from evidence_vault.application.llm_ports import ChatClient, LlmCacheStore
from evidence_vault.application.markdown import card_link, escape, generated_frontmatter
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.slugs import slugify
from evidence_vault.domain.source import CaptureId, SourceId, SourcePacket

SYSTEM_PROMPT = (
    "You identify the single work (film, series, or anime) that a saved TikTok item is about. "
    "Return ONLY one JSON object, no prose and no code fences, with this exact shape: "
    '{"title": string, "year": string or null, "kind": "film"|"series"|"anime"|"unknown", '
    '"confidence": "high"|"medium"|"low", "note": string}. '
    "title is the normalized work title. year is the release year as a string, or null. "
    "kind is the work type. confidence reflects how certain the identification is. "
    "note is a short remark. If the item is not about a film, series, or anime, "
    "return an empty title."
)

_KINDS = frozenset({"film", "series", "anime", "unknown"})
_CONFIDENCES = frozenset({"high", "medium", "low"})
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
_STRIP_CHARS = "\"'\u00ab\u00bb\u201c\u201d\u2018\u2019()[]{}.,;:!?\u2014\u2013-"


def normalize_title(title: str) -> str:
    text = " ".join(title.split()).casefold()
    text = text.strip(_STRIP_CHARS)
    return " ".join(text.split())


def _is_resolved(entry: LlmCatalogEntry) -> bool:
    return bool(normalize_title(entry.title))


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_year(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


@dataclass(frozen=True)
class LlmCatalogEntry:
    title: str
    year: str | None
    kind: str
    confidence: str
    note: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, JsonValue]) -> LlmCatalogEntry:
        kind = _as_str(payload.get("kind")).casefold()
        confidence = _as_str(payload.get("confidence")).casefold()
        return cls(
            title=_as_str(payload.get("title")),
            year=_as_year(payload.get("year")),
            kind=kind if kind in _KINDS else "unknown",
            confidence=confidence if confidence in _CONFIDENCES else "low",
            note=_as_str(payload.get("note")),
        )

    @classmethod
    def from_text(cls, text: str) -> LlmCatalogEntry:
        return cls.from_payload(extract_json_object(text))

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "title": self.title,
            "year": self.year,
            "kind": self.kind,
            "confidence": self.confidence,
            "note": self.note,
        }


@dataclass(frozen=True)
class LlmCatalogQuery:
    collection: str
    limit: int | None = None
    refresh: bool = False
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.collection.strip():
            raise ValueError("collection name is required")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")


@dataclass(frozen=True)
class LlmCatalogItemResult:
    source_id: SourceId
    capture_id: CaptureId
    status: Literal["processed", "skipped_cached", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class LlmCatalogReport:
    collection: str
    model: str
    processed: int
    skipped_cached: int
    unresolved: int
    failed: int
    catalog_size: int
    dry_run: bool
    note: GeneratedDocument | None
    results: tuple[LlmCatalogItemResult, ...]


def _cache_key(packet: SourcePacket, user_prompt: str) -> str:
    digest = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()[:16]
    return f"{packet.source_id}__{digest}"


@dataclass(frozen=True)
class _WorkGroup:
    title: str
    year: str | None
    kind: str
    confidence: str
    packets: tuple[SourcePacket, ...]


def _group_entries(
    entries: list[tuple[SourcePacket, LlmCatalogEntry]],
) -> list[_WorkGroup]:
    groups: dict[tuple[str, str], list[tuple[SourcePacket, LlmCatalogEntry]]] = {}
    for packet, entry in entries:
        normalized = normalize_title(entry.title)
        if not normalized:
            continue
        key = (normalized, (entry.year or "").strip())
        groups.setdefault(key, []).append((packet, entry))
    result: list[_WorkGroup] = []
    for key in sorted(groups):
        items = groups[key]
        result.append(
            _WorkGroup(
                title=items[0][1].title,
                year=next((entry.year for _, entry in items if entry.year), None),
                kind=next((entry.kind for _, entry in items if entry.kind != "unknown"), "unknown"),
                confidence=max(
                    (entry.confidence for _, entry in items),
                    key=lambda value: _CONFIDENCE_RANK.get(value, 0),
                ),
                packets=tuple(packet for packet, _ in items),
            )
        )
    return result


def _render_note(
    *,
    collection: str,
    slug: str,
    model: str,
    generated_at: datetime,
    generated_prefix: str,
    entries: list[tuple[SourcePacket, LlmCatalogEntry]],
    failures: list[tuple[SourcePacket, str]],
) -> GeneratedDocument:
    groups = _group_entries(entries)
    unresolved = [(packet, entry) for packet, entry in entries if not _is_resolved(entry)]
    unresolved.sort(key=lambda item: item[0].source_id)
    frontmatter = generated_frontmatter(
        {
            "collection": collection,
            "slug": slug,
            "model": model,
            "catalog_size": len(groups),
            "video_count": len(entries),
            "generated_at": generated_at.isoformat(),
            "provenance": "agent_inference",
        },
        schema_version="llm-catalog/v1",
        generator_version="1",
    )
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {escape(collection, table=True)} — LLM catalog",
        "",
        "> [!warning] Machine inference",
        "> Derived, rebuildable agent inference, not creator_claim. "
        "Verify against the source cards before relying on it.",
        "",
        f"## Works ({len(groups)})",
        "",
    ]
    if groups:
        for group in groups:
            heading = f"{group.title} ({group.year})" if group.year else group.title
            lines.append(f"### {escape(heading, table=True)}")
            lines.append("")
            lines.append(f"- Kind: {escape(group.kind, table=True)}")
            lines.append(f"- Confidence: {escape(group.confidence, table=True)}")
            lines.append(f"- Videos: {len(group.packets)}")
            links = ", ".join(card_link(generated_prefix, packet) for packet in group.packets)
            lines.append(f"- Sources: {links}")
            lines.append("")
    else:
        lines.extend(["No works identified.", ""])

    lines.extend([f"## Unresolved / not a work ({len(unresolved)})", ""])
    if unresolved:
        for packet, entry in unresolved:
            note = entry.note or entry.title
            suffix = f": {escape(note, table=True)}" if note else ""
            lines.append(f"- {card_link(generated_prefix, packet)}{suffix}")
    else:
        lines.append("None.")
    lines.append("")

    if failures:
        lines.extend(["## Failed", ""])
        lines.extend(
            f"- {card_link(generated_prefix, packet)}: {escape(error, table=True)}"
            for packet, error in failures
        )
        lines.append("")

    return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))


class LlmCatalogRunner:
    NAMESPACE = "llm-catalog"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        client: ChatClient,
        cache: LlmCacheStore,
        model: str,
        generated_prefix: str = "_generated/evidence-vault",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._client = client
        self._cache = cache
        self._model = model
        self._generated_prefix = generated_prefix.strip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, query: LlmCatalogQuery) -> LlmCatalogReport:
        selected = self._select(query)
        results: list[LlmCatalogItemResult] = []
        entries: list[tuple[SourcePacket, LlmCatalogEntry]] = []
        failures: list[tuple[SourcePacket, str]] = []
        for packet in selected:
            user_prompt = _build_user_prompt(packet)
            key = _cache_key(packet, user_prompt)
            cached = None
            if not query.refresh:
                try:
                    cached = self._cache.load(key)
                except Exception:
                    cached = None
            if cached is not None:
                entries.append((packet, LlmCatalogEntry.from_payload(cached)))
                results.append(self._result(packet, "skipped_cached"))
                continue
            if query.dry_run:
                results.append(self._result(packet, "processed"))
                continue
            try:
                text = self._client.complete(SYSTEM_PROMPT, user_prompt)
                entry = LlmCatalogEntry.from_text(text)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                failures.append((packet, message))
                results.append(self._result(packet, "failed", error=message))
                continue
            self._cache.save(key, entry.to_payload())
            entries.append((packet, entry))
            results.append(self._result(packet, "processed"))

        resolved = [(packet, entry) for packet, entry in entries if normalize_title(entry.title)]
        catalog_size = len(
            {(normalize_title(entry.title), (entry.year or "").strip()) for _, entry in resolved}
        )
        note = None
        if not query.dry_run:
            note = _render_note(
                collection=query.collection,
                slug=slugify(query.collection),
                model=self._model,
                generated_at=self._clock(),
                generated_prefix=self._generated_prefix,
                entries=entries,
                failures=failures,
            )
        return LlmCatalogReport(
            collection=query.collection,
            model=self._model,
            processed=sum(item.status == "processed" for item in results),
            skipped_cached=sum(item.status == "skipped_cached" for item in results),
            unresolved=len(entries) - len(resolved),
            failed=sum(item.status == "failed" for item in results),
            catalog_size=catalog_size,
            dry_run=query.dry_run,
            note=note,
            results=tuple(results),
        )

    def _select(self, query: LlmCatalogQuery) -> list[SourcePacket]:
        collection = query.collection.casefold()
        latest = latest_capture_per_source(self._packets.iter_packets())
        selected = [
            packet
            for packet in latest.values()
            if collection in {item.name.casefold() for item in packet.collections}
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
    ) -> LlmCatalogItemResult:
        return LlmCatalogItemResult(
            source_id=packet.source_id,
            capture_id=packet.capture_id,
            status=status,
            error=error,
        )
