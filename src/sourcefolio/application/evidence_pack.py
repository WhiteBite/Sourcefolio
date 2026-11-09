from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, model_validator

from sourcefolio.application.extraction_ports import ExtractionRepository
from sourcefolio.application.ports import (
    ArtifactLocator,
    ArtifactStore,
    SourcePacketRepository,
)
from sourcefolio.application.search import SearchIndexBuilder, SearchResult
from sourcefolio.domain.common import (
    ContractModel,
    NonEmptyStr,
    Provenance,
    UtcDateTime,
)
from sourcefolio.domain.extraction import (
    EvidenceLocator,
    ExtractionId,
    ExtractionKind,
    ExtractionRecord,
    ExtractionRequestId,
    LocatorKind,
)
from sourcefolio.domain.source import (
    ArtifactId,
    ArtifactRef,
    CaptureId,
    CaptureStatus,
    SourceId,
    SourceKind,
    SourcePacket,
)

_TERM = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class EvidencePackExcerpt:
    record: ExtractionRecord
    matched_query: bool


@dataclass(frozen=True)
class EvidencePackSource:
    rank: int
    result: SearchResult
    packet: SourcePacket
    excerpts: tuple[EvidencePackExcerpt, ...]


@dataclass(frozen=True)
class EvidencePack:
    query: str
    generated_at: datetime
    collection: str | None
    kind: SourceKind | None
    status: CaptureStatus | None
    limit: int
    excerpt_limit: int
    max_text_artifact_bytes: int
    sources: tuple[EvidencePackSource, ...]
    limitations: tuple[str, ...]


class EvidencePackFilters(ContractModel):
    collection: str | None = None
    kind: SourceKind | None = None
    status: CaptureStatus | None = None
    limit: int = Field(ge=1)
    excerpt_limit: int = Field(ge=0)
    max_text_artifact_bytes: int = Field(ge=1)


class EvidencePackLocator(ContractModel):
    kind: LocatorKind
    slide: int | None = Field(default=None, ge=1)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    canonical: NonEmptyStr

    @model_validator(mode="after")
    def canonical_matches_typed_locator(self) -> EvidencePackLocator:
        locator = EvidenceLocator(
            kind=self.kind,
            slide=self.slide,
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
            start_char=self.start_char,
            end_char=self.end_char,
        )
        if self.canonical != locator.canonical:
            raise ValueError("canonical locator does not match typed fields")
        return self


class EvidencePackArtifact(ArtifactRef):
    uri: NonEmptyStr


class EvidencePackExcerptDocument(ContractModel):
    extraction_id: ExtractionId
    request_id: ExtractionRequestId
    source_id: SourceId
    capture_id: CaptureId
    kind: ExtractionKind
    created_at: UtcDateTime
    artifact_id: ArtifactId
    artifact_uri: NonEmptyStr
    locator: EvidencePackLocator
    matched_query: bool
    excerpt: str
    extractor: Provenance
    config: dict[str, JsonValue] = Field(default_factory=dict)
    limitations: tuple[NonEmptyStr, ...] = ()


class EvidencePackSourceDocument(ContractModel):
    rank: int = Field(ge=1)
    source_id: SourceId
    capture_id: CaptureId
    external_id: NonEmptyStr | None = None
    title: NonEmptyStr
    canonical_url: NonEmptyStr | None = None
    kind: SourceKind
    status: CaptureStatus
    collections: tuple[NonEmptyStr, ...] = ()
    captured_at: UtcDateTime
    relevance_score: int = Field(ge=0)
    snippet: str
    collector: Provenance
    capture_limitations: tuple[NonEmptyStr, ...] = ()
    artifacts: tuple[EvidencePackArtifact, ...] = Field(min_length=1)
    excerpts: tuple[EvidencePackExcerptDocument, ...] = ()

    @model_validator(mode="after")
    def excerpts_belong_to_source(self) -> EvidencePackSourceDocument:
        artifact_ids = {artifact.artifact_id for artifact in self.artifacts}
        for excerpt in self.excerpts:
            if (excerpt.source_id, excerpt.capture_id) != (self.source_id, self.capture_id):
                raise ValueError("excerpt source/capture does not match its parent")
            if excerpt.artifact_id not in artifact_ids:
                raise ValueError("excerpt artifact is not present in the source packet")
        return self


class EvidencePackDocument(ContractModel):
    schema_version: Literal["evidence-pack/v1"] = "evidence-pack/v1"
    pack_type: Literal["retrieval_only"] = "retrieval_only"
    query: str = Field(min_length=1)
    generated_at: UtcDateTime
    filters: EvidencePackFilters
    result_count: int = Field(ge=0)
    limitations: tuple[NonEmptyStr, ...] = Field(min_length=1)
    sources: tuple[EvidencePackSourceDocument, ...] = ()

    @model_validator(mode="after")
    def counts_and_ranks_match(self) -> EvidencePackDocument:
        if self.result_count != len(self.sources):
            raise ValueError("result_count does not match sources")
        if tuple(source.rank for source in self.sources) != tuple(range(1, len(self.sources) + 1)):
            raise ValueError("source ranks must be contiguous and one-based")
        return self


class EvidencePackBuilder:
    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactStore,
        artifact_locator: ArtifactLocator,
        extractions: ExtractionRepository | None = None,
        max_text_artifact_bytes: int = 1024 * 1024,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._artifacts = artifacts
        self._artifact_locator = artifact_locator
        self._extractions = extractions
        self._max_text_bytes = max_text_artifact_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

    def build(
        self,
        query: str,
        *,
        limit: int = 10,
        collection: str | None = None,
        kind: SourceKind | None = None,
        status: CaptureStatus | None = None,
        excerpt_limit: int = 3,
    ) -> EvidencePack:
        if excerpt_limit < 0:
            raise ValueError("excerpt_limit cannot be negative")
        index = SearchIndexBuilder(
            packets=self._packets,
            artifacts=self._artifacts,
            extractions=self._extractions,
            max_text_artifact_bytes=self._max_text_bytes,
        ).build()
        results = index.search(
            query,
            limit=limit,
            collection=collection,
            kind=kind,
            status=status,
        )
        terms = [term.casefold() for term in _TERM.findall(query)]
        sources: list[EvidencePackSource] = []
        omitted_excerpts = 0
        for rank, result in enumerate(results, start=1):
            packet = self._packets.get(result.source_id, result.capture_id)
            if packet is None:
                raise ValueError("search result refers to a missing source packet")
            records = list(
                self._extractions.iter_for_capture(packet.capture_id)
                if self._extractions is not None
                else ()
            )
            ranked_records = sorted(
                (
                    EvidencePackExcerpt(
                        record=record,
                        matched_query=_matches(record.text, terms),
                    )
                    for record in records
                ),
                key=lambda item: (
                    not item.matched_query,
                    -_term_frequency(item.record.text, terms),
                    item.record.request.locator.canonical,
                    item.record.extraction_id,
                ),
            )
            omitted_excerpts += max(0, len(ranked_records) - excerpt_limit)
            sources.append(
                EvidencePackSource(
                    rank=rank,
                    result=result,
                    packet=packet,
                    excerpts=tuple(ranked_records[:excerpt_limit]),
                )
            )

        limitations = [
            "This is a retrieval pack, not an answer or a claim-verification result.",
            "Search scores are lexical relevance only; they do not measure truth or "
            "source quality.",
            "Derived excerpts may contain OCR or ASR recognition errors and must be "
            "checked against the linked artifact.",
            "Artifact links are local URIs and require access to the archive that "
            "produced this pack.",
        ]
        if omitted_excerpts:
            limitations.append(
                f"{omitted_excerpts} additional derived excerpts were omitted by the "
                "per-source excerpt limit."
            )
        if not sources:
            limitations.append("No archived source matched the query and filters.")
        return EvidencePack(
            query=query,
            generated_at=self._clock(),
            collection=collection,
            kind=kind,
            status=status,
            limit=limit,
            excerpt_limit=excerpt_limit,
            max_text_artifact_bytes=self._max_text_bytes,
            sources=tuple(sources),
            limitations=tuple(limitations),
        )


def _matches(text: str, terms: list[str]) -> bool:
    folded = text.casefold()
    return bool(terms) and all(term in folded for term in terms)


def _term_frequency(text: str, terms: list[str]) -> int:
    folded = text.casefold()
    return sum(folded.count(term) for term in terms)


def evidence_pack_to_dict(
    pack: EvidencePack,
    *,
    artifact_locator: ArtifactLocator,
) -> dict[str, object]:
    payload = {
        "schema_version": "evidence-pack/v1",
        "pack_type": "retrieval_only",
        "query": pack.query,
        "generated_at": pack.generated_at.isoformat(),
        "filters": {
            "collection": pack.collection,
            "kind": pack.kind.value if pack.kind else None,
            "status": pack.status.value if pack.status else None,
            "limit": pack.limit,
            "excerpt_limit": pack.excerpt_limit,
            "max_text_artifact_bytes": pack.max_text_artifact_bytes,
        },
        "result_count": len(pack.sources),
        "limitations": list(pack.limitations),
        "sources": [
            {
                "rank": source.rank,
                "source_id": source.result.source_id,
                "capture_id": source.result.capture_id,
                "external_id": source.result.external_id,
                "title": source.result.title,
                "canonical_url": source.result.canonical_url,
                "kind": source.result.kind.value,
                "status": source.result.status.value,
                "collections": list(source.result.collections),
                "captured_at": source.result.captured_at.isoformat(),
                "relevance_score": source.result.score,
                "snippet": source.result.snippet,
                "collector": source.packet.collector.model_dump(mode="json"),
                "capture_limitations": list(source.packet.limitations),
                "artifacts": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "uri": artifact_locator.uri(artifact.artifact_id),
                        "role": artifact.role.value,
                        "media_type": artifact.media_type,
                        "size_bytes": artifact.size_bytes,
                        "sha256": artifact.sha256,
                        "original_filename": artifact.original_filename,
                    }
                    for artifact in source.packet.artifacts
                ],
                "excerpts": [
                    {
                        "extraction_id": excerpt.record.extraction_id,
                        "request_id": excerpt.record.request_id,
                        "source_id": excerpt.record.request.source_id,
                        "capture_id": excerpt.record.request.capture_id,
                        "kind": excerpt.record.request.kind.value,
                        "created_at": excerpt.record.created_at.isoformat(),
                        "artifact_id": excerpt.record.request.artifact_id,
                        "artifact_uri": artifact_locator.uri(excerpt.record.request.artifact_id),
                        "locator": {
                            **excerpt.record.request.locator.model_dump(mode="json"),
                            "canonical": excerpt.record.request.locator.canonical,
                        },
                        "matched_query": excerpt.matched_query,
                        "excerpt": excerpt.record.excerpt,
                        "extractor": excerpt.record.request.extractor.model_dump(mode="json"),
                        "config": excerpt.record.request.config,
                        "limitations": list(excerpt.record.limitations),
                    }
                    for excerpt in source.excerpts
                ],
            }
            for source in pack.sources
        ],
    }
    document = EvidencePackDocument.model_validate(payload)
    return document.model_dump(mode="json")


def _md(value: object) -> str:
    text = html.escape(" ".join(str(value).split()), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def _link(label: str, uri: str) -> str:
    if urlsplit(uri).scheme not in {"http", "https", "file"}:
        return f"`{_md(uri)}`"
    destination = uri.replace(" ", "%20").replace("<", "%3C").replace(">", "%3E")
    return f"[{_md(label)}](<{destination}>)"


def render_evidence_pack_markdown(
    pack: EvidencePack,
    *,
    artifact_locator: ArtifactLocator,
) -> str:
    lines = [
        "# Evidence pack",
        "",
        f"- Query: `{_md(pack.query)}`",
        f"- Generated: `{pack.generated_at.isoformat()}`",
        f"- Limits: results `{pack.limit}`, excerpts/source `{pack.excerpt_limit}`, "
        f"text bytes `{pack.max_text_artifact_bytes}`",
        "- Mode: **retrieval only; no answer or claim verification is generated**",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {_md(limitation)}" for limitation in pack.limitations)
    for source in pack.sources:
        result = source.result
        packet = source.packet
        lines.extend(
            [
                "",
                f"## {source.rank}. {_md(result.title)}",
                "",
                f"- Source ID: `{result.source_id}`",
                f"- Capture ID: `{result.capture_id}`",
                f"- Kind / status: `{result.kind.value}` / `{result.status.value}`",
                f"- Relevance score: **{result.score}** (not trust)",
                f"- Captured: `{result.captured_at.isoformat()}`",
                f"- Collector: `{_md(packet.collector.actor_id)}` ({_md(packet.collector.method)})",
                f"- Collections: {_md(', '.join(result.collections) or '—')}",
                f"- Search snippet: {_md(result.snippet)}",
            ]
        )
        if result.external_id:
            lines.append(f"- External ID: `{_md(result.external_id)}`")
        if result.canonical_url:
            lines.append(f"- Canonical URL: {_link(result.canonical_url, result.canonical_url)}")
        if packet.limitations:
            lines.append(f"- Capture limitations: {_md('; '.join(packet.limitations))}")
        lines.extend(["", "### Artifacts", ""])
        for artifact in packet.artifacts:
            lines.append(
                f"- {_md(artifact.role.value)}: "
                f"{_link(artifact.artifact_id, artifact_locator.uri(artifact.artifact_id))} "
                f"(SHA-256 `{artifact.sha256}`)"
            )
        lines.extend(["", "### Derived excerpts", ""])
        if not source.excerpts:
            lines.append("None available for this capture.")
        for excerpt in source.excerpts:
            record = excerpt.record
            request = record.request
            extractor = (
                f"{_md(request.extractor.actor_id)} / {_md(request.extractor.version or 'unknown')}"
            )
            artifact_link = _link(
                request.artifact_id,
                artifact_locator.uri(request.artifact_id),
            )
            lines.extend(
                [
                    f"#### {_md(request.kind.value)} · `{_md(request.locator.canonical)}`",
                    "",
                    f"- Extraction ID: `{record.extraction_id}`",
                    f"- Request ID: `{record.request_id}`",
                    f"- Matched query: **{str(excerpt.matched_query).lower()}**",
                    f"- Extractor: `{extractor}`",
                    f"- Artifact: {artifact_link}",
                    f"- Excerpt: {_md(record.excerpt)}",
                ]
            )
            if record.limitations:
                lines.append(f"- Extraction limitations: {_md('; '.join(record.limitations))}")
            lines.append("")
    lines.append("")
    return "\n".join(lines)
