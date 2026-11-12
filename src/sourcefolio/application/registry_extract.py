from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import JsonValue

from sourcefolio.application.entity_extraction import iter_repositories
from sourcefolio.application.extraction_ports import ExtractionRepository
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.llm_enrich import extract_json_object
from sourcefolio.application.llm_ports import ChatClient, LlmCacheStore
from sourcefolio.application.ports import ArtifactStore, SourcePacketRepository
from sourcefolio.application.project_fit import Project
from sourcefolio.application.registry_ports import RegistryRouteStore
from sourcefolio.application.registry_rank import rank_captures
from sourcefolio.application.repo_catalog import parse_repo_ref
from sourcefolio.application.saved_index import saved_comments, saved_index, saved_str
from sourcefolio.application.slugs import slugify
from sourcefolio.domain.common import ActorKind, Provenance, content_id
from sourcefolio.domain.registry import (
    AnchorField,
    CandidateKind,
    CandidateMention,
    FitLevel,
    MentionAnchor,
    RegistryRoute,
    RouteVerdict,
)
from sourcefolio.domain.source import ArtifactRole, CaptureId, SourceId, SourcePacket

SYSTEM_PROMPT = (
    "You extract engineering developments from one saved TikTok item for ONE user "
    "project.\n"
    "Return ONLY one JSON object, no prose and no code fences, with this exact shape:\n"
    '{"verdict": "candidate"|"irrelevant"|"insufficient_signal",\n'
    ' "reason": string,\n'
    ' "mentions": [{"name": string, '
    '"kind": "tool"|"library"|"service"|"technique"|"pattern"|"model"|"dataset"|'
    '"repository"|"topic"|"other", "fit": "high"|"medium"|"low", '
    '"quote": string, "field": "description"|"comment"|"ocr"|"caption"|"title", '
    '"comment_index": integer|null}]}\n'
    "Rules:\n"
    "- A mention is a concrete thing the user could adopt in the project.\n"
    "- quote MUST be copied character-for-character from the cited field. No paraphrase, "
    "no ellipsis.\n"
    '- field "comment" requires comment_index (0-based, as printed in the item).\n'
    "- Return at most 3 mentions, most relevant first.\n"
    '- "insufficient_signal" when the text does not let you judge; "irrelevant" only when '
    'it is readable and not useful; otherwise "candidate".\n'
    "- Never invent facts that are absent from the item."
)

_MAX_ATTEMPTS = 3
_MAX_MENTIONS = 3
_MIN_QUOTE = 8
_MAX_QUOTE = 500
_MIN_SIGNAL_CHARS = 40
_MAX_KEY = 200
_DRY_RUN_PREVIEWS = 2
_FIT_RANK: dict[FitLevel, int] = {FitLevel.HIGH: 3, FitLevel.MEDIUM: 2, FitLevel.LOW: 1}


@dataclass(frozen=True)
class RegistryExtractQuery:
    projects: tuple[Project, ...]
    project_ids: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    limit: int | None = None
    max_ocr_kb: int = 20
    max_caption_kb: int = 20
    refresh: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class RegistryExtractItem:
    source_id: SourceId
    capture_id: CaptureId
    project_id: str
    status: Literal["processed", "skipped", "failed"]
    verdict: str | None
    error: str | None


@dataclass(frozen=True)
class RegistryExtractReport:
    projects: tuple[str, ...]
    model: str
    items_scanned: int
    routes_processed: int
    skipped_cached: int
    failed: int
    verdicts: dict[str, int]
    mentions: int
    dropped_anchors: int
    dry_run: bool
    previews: tuple[tuple[str, str], ...]
    results: tuple[RegistryExtractItem, ...]


@dataclass(frozen=True)
class _FieldTexts:
    description: str
    comments: tuple[str, ...]
    ocr: str
    caption: str


@dataclass(frozen=True)
class _Outcome:
    item: RegistryExtractItem
    mentions: int = 0
    dropped: int = 0
    preview: tuple[str, str] | None = None


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _strip_caption(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "WEBVTT" or stripped.startswith("NOTE"):
            continue
        if "-->" in stripped or stripped.isdigit():
            continue
        kept.append(stripped)
    return " ".join(kept)


def _fingerprint(model: str, user_prompt: str) -> str:
    material = f"{model}\x00{user_prompt}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _signal_length(texts: _FieldTexts) -> int:
    return (
        len(texts.description.strip())
        + sum(len(comment.strip()) for comment in texts.comments)
        + len(texts.ocr.strip())
        + len(texts.caption.strip())
    )


def _build_user_prompt(packet: SourcePacket, project: Project, texts: _FieldTexts) -> str:
    lines = [f"Project: {project.id} | {project.name} | {project.description}"]
    profile = project.profile.strip()
    if profile:
        lines.append(f"  Profile: {profile}")
    lines.append(f"URL: {packet.source.canonical_url or 'unknown'}")
    lines.append(f"Description: {texts.description}")
    if any(comment.strip() for comment in texts.comments):
        lines.append("Comments:")
        for index, comment in enumerate(texts.comments):
            if comment.strip():
                lines.append(f"[{index}] {comment}")
    if texts.ocr:
        lines.append("OCR:")
        lines.append(texts.ocr)
    if texts.caption:
        lines.append("Captions:")
        lines.append(texts.caption)
    return "\n".join(lines)


def _kind(value: object) -> CandidateKind:
    if isinstance(value, str):
        try:
            return CandidateKind(value)
        except ValueError:
            return CandidateKind.OTHER
    return CandidateKind.OTHER


def _fit(value: object) -> FitLevel:
    if isinstance(value, str):
        try:
            return FitLevel(value)
        except ValueError:
            return FitLevel.LOW
    return FitLevel.LOW


def _field(value: object) -> AnchorField | None:
    if isinstance(value, str):
        try:
            return AnchorField(value)
        except ValueError:
            return None
    return None


def _verdict(payload: Mapping[str, JsonValue]) -> tuple[RouteVerdict, str]:
    raw = payload.get("verdict")
    if not isinstance(raw, str):
        return RouteVerdict.INSUFFICIENT_SIGNAL, "invalid model response"
    try:
        verdict = RouteVerdict(raw)
    except ValueError:
        return RouteVerdict.INSUFFICIENT_SIGNAL, "invalid model response"
    reason = _as_str(payload.get("reason"))
    if verdict == RouteVerdict.INSUFFICIENT_SIGNAL and not reason:
        reason = "insufficient signal"
    return verdict, reason


def _candidate_key(name: str) -> str:
    return slugify(name)[:_MAX_KEY]


def _mention(
    item: Mapping[str, object],
    texts: _FieldTexts,
    packet: SourcePacket,
) -> CandidateMention | None:
    name = _as_str(item.get("name"))
    if not name:
        return None
    kind = _kind(item.get("kind"))
    fit = _fit(item.get("fit"))
    field = _field(item.get("field"))
    if field is None:
        return None
    quote = _as_str(item.get("quote"))
    if len(quote) < _MIN_QUOTE or len(quote) > _MAX_QUOTE:
        return None
    comment_index: int | None = None
    if field == AnchorField.COMMENT:
        raw_index = item.get("comment_index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            return None
        if raw_index < 0 or raw_index >= len(texts.comments):
            return None
        comment_index = raw_index
    if field == AnchorField.DESCRIPTION:
        cited = texts.description
    elif field == AnchorField.COMMENT:
        if comment_index is None:
            return None
        cited = texts.comments[comment_index]
    elif field == AnchorField.TITLE:
        cited = packet.title or ""
    else:
        return None
    start = cited.find(quote)
    if start < 0:
        return None
    anchor = MentionAnchor(
        field=field,
        quote=quote,
        start_char=start,
        end_char=start + len(quote),
        comment_index=comment_index,
    )
    key = _candidate_key(name)
    body: dict[str, object] = {
        "candidate_key": key,
        "kind": kind,
        "title": name,
        "fit": fit,
        "anchor": anchor.model_dump(mode="json"),
    }
    return CandidateMention(
        mention_id=content_id("men", body),
        candidate_key=key,
        kind=kind,
        title=name,
        fit=fit,
        anchor=anchor,
    )


def _llm_mentions(
    value: JsonValue,
    texts: _FieldTexts,
    packet: SourcePacket,
) -> tuple[tuple[CandidateMention, ...], int]:
    if not isinstance(value, list):
        return (), 0
    mentions: list[CandidateMention] = []
    dropped = 0
    for item in value[:_MAX_MENTIONS]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        mention = _mention(item, texts, packet)
        if mention is None:
            dropped += 1
            continue
        mentions.append(mention)
    return tuple(mentions), dropped


def _repository_mentions(
    texts: _FieldTexts,
    existing: set[str],
) -> tuple[CandidateMention, ...]:
    seen = set(existing)
    sources: list[tuple[AnchorField, int | None, str]] = [
        (AnchorField.DESCRIPTION, None, texts.description)
    ]
    sources.extend(
        (AnchorField.COMMENT, index, comment) for index, comment in enumerate(texts.comments)
    )
    mentions: list[CandidateMention] = []
    for field, comment_index, text in sources:
        for match in iter_repositories(text):
            ref = parse_repo_ref(match.url)
            if ref is None:
                continue
            key = f"repo:{ref.key}"
            if key in seen or len(key) > _MAX_KEY:
                continue
            quote = text[match.start_char : match.end_char]
            if len(quote) < _MIN_QUOTE or len(quote) > _MAX_QUOTE:
                continue
            seen.add(key)
            anchor = MentionAnchor(
                field=field,
                quote=quote,
                start_char=match.start_char,
                end_char=match.end_char,
                comment_index=comment_index,
            )
            body: dict[str, object] = {
                "candidate_key": key,
                "kind": CandidateKind.REPOSITORY,
                "title": match.url,
                "fit": FitLevel.LOW,
                "anchor": anchor.model_dump(mode="json"),
            }
            mentions.append(
                CandidateMention(
                    mention_id=content_id("men", body),
                    candidate_key=key,
                    kind=CandidateKind.REPOSITORY,
                    title=match.url,
                    fit=FitLevel.LOW,
                    anchor=anchor,
                )
            )
    return tuple(mentions)


def _interpret(
    payload: Mapping[str, JsonValue],
    texts: _FieldTexts,
    packet: SourcePacket,
) -> tuple[RouteVerdict, str, tuple[CandidateMention, ...], int]:
    verdict, reason = _verdict(payload)
    if verdict != RouteVerdict.CANDIDATE:
        return verdict, reason, (), 0
    mentions, dropped = _llm_mentions(payload.get("mentions"), texts, packet)
    repo_mentions = _repository_mentions(texts, {mention.candidate_key for mention in mentions})
    combined = mentions + repo_mentions
    if not combined:
        return RouteVerdict.INSUFFICIENT_SIGNAL, "no verbatim anchor", (), dropped
    return verdict, reason, combined, dropped


def _best_fit(mentions: tuple[CandidateMention, ...]) -> FitLevel:
    return max(
        (mention.fit for mention in mentions),
        key=lambda fit: _FIT_RANK[fit],
        default=FitLevel.LOW,
    )


class RegistryExtractor:
    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactStore,
        extractions: ExtractionRepository,
        client: ChatClient,
        cache: LlmCacheStore,
        routes: RegistryRouteStore,
        projects: tuple[Project, ...],
        model: str,
        clock: Callable[[], datetime],
        retry_backoff_seconds: float = 0.0,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self._packets = packets
        self._artifacts = artifacts
        self._extractions = extractions
        self._client = client
        self._cache = cache
        self._routes = routes
        self._projects = projects
        self._model = model
        self._clock = clock
        self._retry_backoff = retry_backoff_seconds
        self._progress = progress

    def run(self, query: RegistryExtractQuery) -> RegistryExtractReport:
        selected, pre_ranks = self._select(query)
        projects = self._selected_projects(query)
        verdicts = {verdict.value: 0 for verdict in RouteVerdict}
        results: list[RegistryExtractItem] = []
        previews: list[tuple[str, str]] = []
        mentions_total = 0
        dropped_total = 0
        total = len(selected) * len(projects)
        position = 0
        for packet in selected:
            texts = self._texts(packet, query)
            for project in projects:
                position += 1
                outcome = self._process(
                    packet=packet,
                    project=project,
                    texts=texts,
                    pre_rank=pre_ranks[packet.capture_id],
                    query=query,
                    previews=previews,
                )
                results.append(outcome.item)
                if outcome.preview is not None:
                    previews.append(outcome.preview)
                mentions_total += outcome.mentions
                dropped_total += outcome.dropped
                if outcome.item.verdict is not None:
                    verdicts[outcome.item.verdict] += 1
                self._emit_progress(position, total, outcome.item)
        return RegistryExtractReport(
            projects=tuple(project.id for project in projects),
            model=self._model,
            items_scanned=len(selected),
            routes_processed=sum(item.status == "processed" for item in results),
            skipped_cached=sum(item.status == "skipped" for item in results),
            failed=sum(item.status == "failed" for item in results),
            verdicts=verdicts,
            mentions=mentions_total,
            dropped_anchors=dropped_total,
            dry_run=query.dry_run,
            previews=tuple(previews),
            results=tuple(results),
        )

    def _process(
        self,
        *,
        packet: SourcePacket,
        project: Project,
        texts: _FieldTexts,
        pre_rank: int,
        query: RegistryExtractQuery,
        previews: list[tuple[str, str]],
    ) -> _Outcome:
        user_prompt = _build_user_prompt(packet, project, texts)
        prompt_fingerprint = _fingerprint(self._model, user_prompt)
        route_id = _route_id(packet, project.id, self._model, prompt_fingerprint)
        if _signal_length(texts) < _MIN_SIGNAL_CHARS:
            route = self._route(
                packet=packet,
                project_id=project.id,
                prompt_fingerprint=prompt_fingerprint,
                verdict=RouteVerdict.INSUFFICIENT_SIGNAL,
                reason="not enough text",
                mentions=(),
                pre_rank=pre_rank,
                provenance=Provenance(
                    actor_kind=ActorKind.TOOL,
                    actor_id="registry-extract",
                    method="metadata",
                ),
            )
            if not query.dry_run:
                self._routes.save(route)
            return _Outcome(
                item=_item(packet, project.id, "processed", RouteVerdict.INSUFFICIENT_SIGNAL)
            )
        if not query.refresh:
            try:
                existing = self._routes.get(route_id)
            except Exception:
                existing = None
            if existing is not None:
                return _Outcome(
                    item=_item(packet, project.id, "skipped", existing.verdict),
                    mentions=len(existing.mentions),
                )
        cache_key = f"{packet.capture_id}__{project.id}__{prompt_fingerprint}"
        payload: Mapping[str, JsonValue] | None = None
        if not query.refresh:
            try:
                payload = self._cache.load(cache_key)
            except Exception:
                payload = None
        if payload is not None:
            return self._cached_outcome(
                packet=packet,
                project_id=project.id,
                prompt_fingerprint=prompt_fingerprint,
                texts=texts,
                payload=payload,
                pre_rank=pre_rank,
                query=query,
            )
        if query.dry_run:
            preview = None
            if len(previews) < _DRY_RUN_PREVIEWS:
                preview = (SYSTEM_PROMPT, user_prompt)
            return _Outcome(
                item=_item(packet, project.id, "processed", None),
                preview=preview,
            )
        model_payload, error = self._complete(user_prompt)
        if model_payload is None:
            return _Outcome(item=_item(packet, project.id, "failed", None, error=error))
        verdict, reason, mentions, dropped = _interpret(model_payload, texts, packet)
        route = self._route(
            packet=packet,
            project_id=project.id,
            prompt_fingerprint=prompt_fingerprint,
            verdict=verdict,
            reason=reason,
            mentions=mentions,
            pre_rank=pre_rank,
            provenance=Provenance(
                actor_kind=ActorKind.AGENT,
                actor_id=self._model,
                method="chat",
            ),
        )
        self._cache.save(cache_key, model_payload)
        self._routes.save(route)
        return _Outcome(
            item=_item(packet, project.id, "processed", verdict),
            mentions=len(mentions),
            dropped=dropped,
        )

    def _cached_outcome(
        self,
        *,
        packet: SourcePacket,
        project_id: str,
        prompt_fingerprint: str,
        texts: _FieldTexts,
        payload: Mapping[str, JsonValue],
        pre_rank: int,
        query: RegistryExtractQuery,
    ) -> _Outcome:
        verdict, reason, mentions, dropped = _interpret(payload, texts, packet)
        route = self._route(
            packet=packet,
            project_id=project_id,
            prompt_fingerprint=prompt_fingerprint,
            verdict=verdict,
            reason=reason,
            mentions=mentions,
            pre_rank=pre_rank,
            provenance=Provenance(
                actor_kind=ActorKind.AGENT,
                actor_id=self._model,
                method="chat",
            ),
        )
        if not query.dry_run:
            self._routes.save(route)
        return _Outcome(
            item=_item(packet, project_id, "skipped", verdict),
            mentions=len(mentions),
            dropped=dropped,
        )

    def _complete(self, user_prompt: str) -> tuple[dict[str, JsonValue] | None, str | None]:
        error: str | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                text = self._client.complete(SYSTEM_PROMPT, user_prompt)
                return extract_json_object(text), None
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                if attempt + 1 < _MAX_ATTEMPTS:
                    time.sleep(self._retry_backoff * (attempt + 1))
        return None, error

    def _route(
        self,
        *,
        packet: SourcePacket,
        project_id: str,
        prompt_fingerprint: str,
        verdict: RouteVerdict,
        reason: str,
        mentions: tuple[CandidateMention, ...],
        pre_rank: int,
        provenance: Provenance,
    ) -> RegistryRoute:
        identity = _route_identity(packet, project_id, self._model, prompt_fingerprint)
        return RegistryRoute(
            route_id=content_id("rt", identity),
            source_id=packet.source_id,
            capture_id=packet.capture_id,
            source_url=packet.source.canonical_url or "",
            source_title=packet.title or "",
            project_id=project_id,
            model=self._model,
            prompt_fingerprint=prompt_fingerprint,
            verdict=verdict,
            reason=reason,
            mentions=mentions,
            fit=_best_fit(mentions),
            pre_rank=max(pre_rank, 0),
            captured_at=packet.captured_at,
            provenance=provenance,
            created_at=self._clock(),
            limitations=(),
        )

    def _texts(self, packet: SourcePacket, query: RegistryExtractQuery) -> _FieldTexts:
        saved = saved_index(packet)
        description = saved_str(saved, "desc") or ""
        comments = tuple(saved_str(comment, "text") or "" for comment in saved_comments(saved))
        return _FieldTexts(
            description=description,
            comments=comments,
            ocr=self._ocr(packet.capture_id, query.max_ocr_kb),
            caption=self._caption(packet, query.max_caption_kb),
        )

    def _ocr(self, capture_id: CaptureId, max_ocr_kb: int) -> str:
        parts: list[str] = []
        for record in self._extractions.iter_for_capture(capture_id):
            text = record.text.strip()
            if text:
                parts.append(text)
        return _truncate_utf8("\n".join(parts), max_ocr_kb * 1024)

    def _caption(self, packet: SourcePacket, max_caption_kb: int) -> str:
        parts: list[str] = []
        for artifact in packet.artifacts:
            if artifact.role != ArtifactRole.CAPTION_TEXT:
                continue
            stream = self._artifacts.open(artifact.artifact_id)
            try:
                raw = stream.read()
            finally:
                stream.close()
            parts.append(raw.decode("utf-8", errors="replace"))
        return _truncate_utf8(_strip_caption("\n".join(parts)), max_caption_kb * 1024)

    def _select(self, query: RegistryExtractQuery) -> tuple[list[SourcePacket], dict[str, int]]:
        latest = latest_capture_per_source(self._packets.iter_packets())
        candidates = list(latest.values())
        if query.collections:
            wanted = {name.casefold() for name in query.collections}
            candidates = [
                packet
                for packet in candidates
                if wanted & {item.name.casefold() for item in packet.collections}
            ]
        ranked = rank_captures(candidates, self._extractions, max_ocr_kb=query.max_ocr_kb)
        by_capture = {packet.capture_id: packet for packet in candidates}
        scores = {score.capture_id: score.score for score in ranked}
        ordered = [
            by_capture[score.capture_id] for score in ranked if score.capture_id in by_capture
        ]
        if query.limit is not None:
            ordered = ordered[: query.limit]
        return ordered, scores

    def _selected_projects(self, query: RegistryExtractQuery) -> tuple[Project, ...]:
        if not query.project_ids:
            return query.projects
        wanted = set(query.project_ids)
        return tuple(project for project in query.projects if project.id in wanted)

    def _emit_progress(self, position: int, total: int, item: RegistryExtractItem) -> None:
        if self._progress is None:
            return
        label = item.verdict or item.status
        self._progress(f"[{position}/{total}] {item.project_id} {label}")


def _route_identity(
    packet: SourcePacket,
    project_id: str,
    model: str,
    prompt_fingerprint: str,
) -> dict[str, str]:
    return {
        "source_id": packet.source_id,
        "capture_id": packet.capture_id,
        "project_id": project_id,
        "model": model,
        "prompt_fingerprint": prompt_fingerprint,
    }


def _route_id(
    packet: SourcePacket,
    project_id: str,
    model: str,
    prompt_fingerprint: str,
) -> str:
    return content_id("rt", _route_identity(packet, project_id, model, prompt_fingerprint))


def _item(
    packet: SourcePacket,
    project_id: str,
    status: Literal["processed", "skipped", "failed"],
    verdict: RouteVerdict | None,
    *,
    error: str | None = None,
) -> RegistryExtractItem:
    return RegistryExtractItem(
        source_id=packet.source_id,
        capture_id=packet.capture_id,
        project_id=project_id,
        status=status,
        verdict=None if verdict is None else verdict.value,
        error=error,
    )
