from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue

from sourcefolio.application.evidence_pack import EvidencePackBuilder, EvidencePackSource
from sourcefolio.application.llm_ports import ChatClient, LlmCacheStore
from sourcefolio.application.markdown import escape, generated_frontmatter
from sourcefolio.application.projection_types import GeneratedDocument
from sourcefolio.application.slugs import slugify
from sourcefolio.domain.source import CaptureStatus, SourceKind

SYSTEM_PROMPT = (
    "You answer questions strictly from the provided excerpts of a local evidence archive. "
    "Use ONLY the excerpts; never invent facts that are not present in them. "
    "Cite the sources you rely on by their rank and source_id, for example [1] src_.... "
    "If the excerpts do not contain enough information, say so explicitly with the phrase "
    '"insufficient evidence" and do not guess. '
    "Your answer is agent inference over retrieved excerpts, not a creator claim, "
    "and relevance is not truth."
)

_INFERENCE_LIMITATION = (
    "Answer is agent inference derived only from retrieved excerpts; relevance is not truth."
)
_RETRIEVAL_ONLY_LIMITATION = "Retrieval-only: no LLM synthesis was requested or available."
_NO_MATCH_LIMITATION = "No archived source matched the query and filters."
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class AskCitation:
    rank: int
    source_id: str
    capture_id: str
    title: str
    url: str | None
    snippet: str


@dataclass(frozen=True)
class AskQuery:
    query: str
    project: str | None = None
    limit: int = 10
    excerpt_limit: int = 3
    no_llm: bool = False
    collection: str | None = None
    kind: SourceKind | None = None
    status: CaptureStatus | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must be non-empty")
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.excerpt_limit < 0:
            raise ValueError("excerpt_limit cannot be negative")


@dataclass(frozen=True)
class AskReport:
    query: str
    generated_at: datetime
    project: str | None
    model: str
    grounded: bool
    status: Literal["ok", "retrieval_only", "degraded"]
    answer: str
    citations: tuple[AskCitation, ...]
    limitations: tuple[str, ...]


def _citation(source: EvidencePackSource) -> AskCitation:
    result = source.result
    return AskCitation(
        rank=source.rank,
        source_id=result.source_id,
        capture_id=result.capture_id,
        title=result.title,
        url=result.canonical_url,
        snippet=result.snippet,
    )


def _cache_key(query: AskQuery, model: str) -> str:
    material = f"{query.query}\x00{model}\x00{query.project or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _cached_answer(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("answer")
    return value if isinstance(value, str) else ""


def _build_user_prompt(
    query: str,
    sources: tuple[EvidencePackSource, ...],
    project_context: str | None,
) -> str:
    lines = [f"Question: {query}"]
    if project_context:
        lines.append(f"Project context: {project_context}")
    lines.append("Excerpts:")
    for source in sources:
        result = source.result
        lines.append(f"[{source.rank}] {result.title} ({result.source_id} / {result.capture_id})")
        if result.canonical_url:
            lines.append(f"URL: {result.canonical_url}")
        if result.snippet:
            lines.append(f"Snippet: {result.snippet}")
        for excerpt in source.excerpts:
            text = excerpt.record.excerpt.strip()
            if text:
                lines.append(f"- {text}")
    return "\n".join(lines)


def _retrieval_summary(report: AskReport) -> str:
    if report.citations:
        return f"No synthesized answer; {len(report.citations)} retrieved source(s) listed below."
    return _NO_MATCH_LIMITATION


class AskRunner:
    CACHE_NAMESPACE = "ask"

    def __init__(
        self,
        *,
        pack_builder: EvidencePackBuilder,
        client: ChatClient | None,
        cache: LlmCacheStore,
        model: str,
        generated_prefix: str = "_generated/sourcefolio",
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self._pack_builder = pack_builder
        self._client = client
        self._cache = cache
        self._model = model
        self._generated_prefix = generated_prefix.strip("/")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._retry_backoff = retry_backoff_seconds

    def run(self, query: AskQuery, *, project_context: str | None = None) -> AskReport:
        pack = self._pack_builder.build(
            query.query,
            limit=query.limit,
            collection=query.collection,
            kind=query.kind,
            status=query.status,
            excerpt_limit=query.excerpt_limit,
        )
        citations = tuple(_citation(source) for source in pack.sources)
        limitations = list(pack.limitations)
        limitations.append(_INFERENCE_LIMITATION)
        generated_at = self._clock()
        client = self._client
        if client is None or query.no_llm or not pack.sources:
            if not pack.sources and _NO_MATCH_LIMITATION not in limitations:
                limitations.append(_NO_MATCH_LIMITATION)
            if pack.sources:
                limitations.append(_RETRIEVAL_ONLY_LIMITATION)
            return self._report(
                query,
                generated_at=generated_at,
                grounded=False,
                status="retrieval_only",
                answer="",
                citations=citations,
                limitations=limitations,
            )
        key = _cache_key(query, self._model)
        cached = self._load_cache(key)
        if cached is not None:
            return self._report(
                query,
                generated_at=generated_at,
                grounded=True,
                status="ok",
                answer=_cached_answer(cached),
                citations=citations,
                limitations=limitations,
            )
        user_prompt = _build_user_prompt(query.query, pack.sources, project_context)
        answer, error = self._complete(client, user_prompt)
        if answer is None:
            limitations.append(
                f"LLM synthesis failed: {error}; retrieval excerpt list returned instead."
            )
            return self._report(
                query,
                generated_at=generated_at,
                grounded=False,
                status="degraded",
                answer="",
                citations=citations,
                limitations=limitations,
            )
        self._cache.save(key, {"answer": answer})
        return self._report(
            query,
            generated_at=generated_at,
            grounded=True,
            status="ok",
            answer=answer,
            citations=citations,
            limitations=limitations,
        )

    def _complete(self, client: ChatClient, user_prompt: str) -> tuple[str | None, str | None]:
        error: str | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return client.complete(SYSTEM_PROMPT, user_prompt), None
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                if attempt + 1 < _MAX_ATTEMPTS:
                    self._sleep(self._retry_backoff * (attempt + 1))
        return None, error

    def _load_cache(self, key: str) -> Mapping[str, JsonValue] | None:
        try:
            return self._cache.load(key)
        except Exception:
            return None

    def _report(
        self,
        query: AskQuery,
        *,
        generated_at: datetime,
        grounded: bool,
        status: Literal["ok", "retrieval_only", "degraded"],
        answer: str,
        citations: tuple[AskCitation, ...],
        limitations: list[str],
    ) -> AskReport:
        return AskReport(
            query=query.query,
            generated_at=generated_at,
            project=query.project,
            model=self._model,
            grounded=grounded,
            status=status,
            answer=answer,
            citations=citations,
            limitations=tuple(limitations),
        )


def render_ask_note(report: AskReport) -> GeneratedDocument:
    filename = f"{report.generated_at.strftime('%Y%m%dT%H%M%S%f')}-{slugify(report.query)}.md"
    extra: dict[str, object] = {
        "query": report.query,
        "model": report.model,
        "grounded": report.grounded,
        "status": report.status,
        "provenance": "agent_inference",
        "generated_at": report.generated_at.isoformat(),
    }
    if report.project is not None:
        extra["project"] = report.project
    frontmatter = generated_frontmatter(extra, schema_version="answer/v1", generator_version="1")
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {escape(report.query)}",
        "",
        "> [!warning] Machine inference",
        "> This answer is derived agent inference over retrieved excerpts, not creator_claim. "
        "Relevance is not truth; verify against the source cards before relying on it.",
        "",
        "## Answer",
        "",
        escape(report.answer) if report.answer else _retrieval_summary(report),
        "",
        "## Citations",
        "",
    ]
    if report.citations:
        for citation in report.citations:
            lines.append(
                f"- [{citation.rank}] {escape(citation.title)} — "
                f"{citation.source_id} / {citation.capture_id}"
            )
            if citation.url:
                lines.append(f"  - URL: {escape(citation.url)}")
            if citation.snippet:
                lines.append(f"  - Snippet: {escape(citation.snippet)}")
    else:
        lines.append("None.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {escape(limitation)}" for limitation in report.limitations)
    lines.extend(
        [
            "",
            "## Offer",
            "",
            "Record a human decision from this answer:",
            "",
            f'sourcefolio decision-new --title "{report.query}" --problem "{report.query}"',
            "",
        ]
    )
    return GeneratedDocument(path=filename, content="\n".join(lines))


def ask_to_dict(report: AskReport) -> dict[str, object]:
    return {
        "schema_version": "answer/v1",
        "query": report.query,
        "generated_at": report.generated_at.isoformat(),
        "project": report.project,
        "model": report.model,
        "grounded": report.grounded,
        "status": report.status,
        "answer": report.answer,
        "citations": [
            {
                "rank": citation.rank,
                "source_id": citation.source_id,
                "capture_id": citation.capture_id,
                "title": citation.title,
                "url": citation.url,
                "snippet": citation.snippet,
            }
            for citation in report.citations
        ],
        "limitations": list(report.limitations),
    }
