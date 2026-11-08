from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import yaml
from pydantic import JsonValue

from evidence_vault.application.collection_index import slugify
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.llm_ports import ChatClient, LlmCacheStore
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.saved_index import (
    saved_comments,
    saved_index,
    saved_str,
)
from evidence_vault.domain.source import CaptureId, SourceId, SourcePacket

SYSTEM_PROMPT = (
    "You enrich saved TikTok items for a local evidence archive. "
    "Return ONLY one JSON object, no prose and no code fences, with this exact shape: "
    '{"title": string, "summary": string, "category": string, '
    '"entities": [{"type": string, "value": string}], "links": [string], "tags": [string]}. '
    "Use the item's own language for title and summary. "
    "category is a single short label. entities are named things (tools, people, orgs). "
    "links are URLs found in the metadata. tags are short topical keywords. "
    "Never invent facts that are absent from the metadata."
)

_MAX_COMMENTS = 10


def _md(value: object) -> str:
    text = html.escape(" ".join(str(value).split()), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def _wikilink_alias(value: str) -> str:
    cleaned = " ".join(value.split())
    for character in ("[", "]", "|", "#", "^"):
        cleaned = cleaned.replace(character, " ")
    return " ".join(cleaned.split()) or "source"


def _balanced_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    for start, character in enumerate(text):
        if character != "{":
            continue
        candidate = _balanced_object(text, start)
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found in LLM response")


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_str_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        text = _as_str(item)
        if text:
            result.append(text)
    return tuple(result)


def _as_entities(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return ()
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entity_type = _as_str(item.get("type"))
        entity_value = _as_str(item.get("value"))
        if entity_type and entity_value:
            result.append((entity_type, entity_value))
    return tuple(result)


@dataclass(frozen=True)
class LlmEnrichment:
    title: str
    summary: str
    category: str
    entities: tuple[tuple[str, str], ...]
    links: tuple[str, ...]
    tags: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, JsonValue]) -> LlmEnrichment:
        return cls(
            title=_as_str(payload.get("title")),
            summary=_as_str(payload.get("summary")),
            category=_as_str(payload.get("category")),
            entities=_as_entities(payload.get("entities")),
            links=_as_str_list(payload.get("links")),
            tags=_as_str_list(payload.get("tags")),
        )

    @classmethod
    def from_text(cls, text: str) -> LlmEnrichment:
        return cls.from_payload(extract_json_object(text))

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "title": self.title,
            "summary": self.summary,
            "category": self.category,
            "entities": [{"type": kind, "value": value} for kind, value in self.entities],
            "links": list(self.links),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class LlmEnrichQuery:
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
class LlmEnrichItemResult:
    source_id: SourceId
    capture_id: CaptureId
    status: Literal["processed", "skipped_cached", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class LlmEnrichReport:
    collection: str
    model: str
    processed: int
    skipped_cached: int
    failed: int
    dry_run: bool
    note: GeneratedDocument | None
    results: tuple[LlmEnrichItemResult, ...]


def _build_user_prompt(packet: SourcePacket) -> str:
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
    return "\n".join(lines)


def _cache_key(packet: SourcePacket, user_prompt: str) -> str:
    digest = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()[:16]
    return f"{packet.source_id}__{digest}"


def _card_link(packet: SourcePacket) -> str:
    target = f"_generated/evidence-vault/source-cards/{packet.source_id}/{packet.capture_id}"
    alias = _wikilink_alias(packet.title or packet.source.external_id or packet.source_id)
    return f"[[{target}|{alias}]]"


def _render_note(
    *,
    collection: str,
    slug: str,
    model: str,
    generated_at: datetime,
    entries: list[tuple[SourcePacket, LlmEnrichment]],
    failures: list[tuple[SourcePacket, str]],
) -> GeneratedDocument:
    properties = {
        "schema_version": "llm-digest/v1",
        "generated": True,
        "generator": "evidence-vault",
        "generator_version": "1",
        "collection": collection,
        "slug": slug,
        "model": model,
        "video_count": len(entries),
        "generated_at": generated_at.isoformat(),
        "provenance": "agent_inference",
    }
    frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {_md(collection)} — LLM digest",
        "",
        "> [!warning] Machine inference",
        "> Derived, rebuildable agent inference, not creator_claim. "
        "Verify against the source cards before relying on it.",
        "",
        f"## Videos ({len(entries)})",
        "",
    ]
    for packet, enrichment in entries:
        title = enrichment.title or packet.title or packet.source.external_id or packet.source_id
        lines.append(f"### {_md(title)}")
        lines.append("")
        lines.append(f"- Source: {_card_link(packet)}")
        if enrichment.category:
            lines.append(f"- Category: {_md(enrichment.category)}")
        if enrichment.tags:
            lines.append(f"- Tags: {_md(', '.join(enrichment.tags))}")
        if enrichment.summary:
            lines.extend(["", _md(enrichment.summary)])
        lines.append("")

    entities: dict[tuple[str, str], None] = {}
    links: dict[str, None] = {}
    categories: dict[str, None] = {}
    for _, enrichment in entries:
        for entity in enrichment.entities:
            entities.setdefault(entity, None)
        for link in enrichment.links:
            links.setdefault(link, None)
        if enrichment.category:
            categories.setdefault(enrichment.category, None)

    lines.extend(["## Entities", ""])
    if entities:
        lines.extend(["| Type | Value |", "|---|---|"])
        lines.extend(
            f"| {_md(kind)} | {_md(value)} |"
            for kind, value in sorted(entities, key=lambda item: (item[0].casefold(), item[1]))
        )
    else:
        lines.append("No entities found.")
    lines.extend(["", "## Links", ""])
    if links:
        lines.extend(f"- {_md(link)}" for link in sorted(links))
    else:
        lines.append("No links found.")
    lines.extend(["", "## Categories", ""])
    if categories:
        lines.extend(f"- {_md(category)}" for category in sorted(categories, key=str.casefold))
    else:
        lines.append("No categories found.")
    lines.append("")

    if failures:
        lines.extend(["## Failed", ""])
        lines.extend(f"- {_card_link(packet)}: {_md(error)}" for packet, error in failures)
        lines.append("")

    return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))


class LlmEnrichRunner:
    NAMESPACE = "llm"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        client: ChatClient,
        cache: LlmCacheStore,
        model: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._client = client
        self._cache = cache
        self._model = model
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, query: LlmEnrichQuery) -> LlmEnrichReport:
        selected = self._select(query)
        results: list[LlmEnrichItemResult] = []
        entries: list[tuple[SourcePacket, LlmEnrichment]] = []
        failures: list[tuple[SourcePacket, str]] = []
        for packet in selected:
            user_prompt = _build_user_prompt(packet)
            key = _cache_key(packet, user_prompt)
            cached = None if query.refresh else self._cache.load(key)
            if cached is not None:
                entries.append((packet, LlmEnrichment.from_payload(cached)))
                results.append(self._result(packet, "skipped_cached"))
                continue
            if query.dry_run:
                results.append(self._result(packet, "processed"))
                continue
            try:
                text = self._client.complete(SYSTEM_PROMPT, user_prompt)
                enrichment = LlmEnrichment.from_text(text)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                failures.append((packet, message))
                results.append(self._result(packet, "failed", error=message))
                continue
            self._cache.save(key, enrichment.to_payload())
            entries.append((packet, enrichment))
            results.append(self._result(packet, "processed"))

        note = None
        if not query.dry_run:
            note = _render_note(
                collection=query.collection,
                slug=slugify(query.collection),
                model=self._model,
                generated_at=self._clock(),
                entries=entries,
                failures=failures,
            )
        return LlmEnrichReport(
            collection=query.collection,
            model=self._model,
            processed=sum(item.status == "processed" for item in results),
            skipped_cached=sum(item.status == "skipped_cached" for item in results),
            failed=sum(item.status == "failed" for item in results),
            dry_run=query.dry_run,
            note=note,
            results=tuple(results),
        )

    def _select(self, query: LlmEnrichQuery) -> list[SourcePacket]:
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
    ) -> LlmEnrichItemResult:
        return LlmEnrichItemResult(
            source_id=packet.source_id,
            capture_id=packet.capture_id,
            status=status,
            error=error,
        )
