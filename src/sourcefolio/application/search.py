from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from sourcefolio.application.extraction_ports import ExtractionRepository
from sourcefolio.application.ports import ArtifactStore, SourcePacketRepository
from sourcefolio.domain.common import canonical_json
from sourcefolio.domain.source import (
    ArtifactRef,
    CaptureId,
    CaptureStatus,
    SourceId,
    SourceKind,
)

_TERM = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class SearchDocument:
    source_id: SourceId
    capture_id: CaptureId
    external_id: str | None
    title: str
    canonical_url: str | None
    kind: SourceKind
    status: CaptureStatus
    collections: tuple[str, ...]
    captured_at: datetime
    content: str


@dataclass(frozen=True)
class SearchResult:
    source_id: SourceId
    capture_id: CaptureId
    external_id: str | None
    title: str
    canonical_url: str | None
    kind: SourceKind
    status: CaptureStatus
    collections: tuple[str, ...]
    captured_at: datetime
    score: int
    snippet: str


class SearchIndexBuilder:
    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactStore,
        extractions: ExtractionRepository | None = None,
        max_text_artifact_bytes: int = 1024 * 1024,
    ) -> None:
        if max_text_artifact_bytes < 1:
            raise ValueError("max_text_artifact_bytes must be positive")
        self._packets = packets
        self._artifacts = artifacts
        self._extractions = extractions
        self._max_bytes = max_text_artifact_bytes

    def build(self) -> LexicalSearchIndex:
        return LexicalSearchIndex(tuple(self._documents()))

    def _documents(self) -> Iterator[SearchDocument]:
        for packet in self._packets.iter_packets():
            parts = [
                packet.title or "",
                packet.source.canonical_url or "",
                packet.source.external_id or "",
                *(collection.name for collection in packet.collections),
                *packet.limitations,
                canonical_json(packet.metadata),
            ]
            parts.extend(self._artifact_text(artifact) for artifact in packet.artifacts)
            if self._extractions is not None:
                for record in self._extractions.iter_for_capture(packet.capture_id):
                    if len(record.text.encode("utf-8")) <= self._max_bytes:
                        parts.extend(
                            (
                                record.request.kind.value,
                                record.request.locator.canonical,
                                record.text,
                            )
                        )
            yield SearchDocument(
                source_id=packet.source_id,
                capture_id=packet.capture_id,
                external_id=packet.source.external_id,
                title=packet.title or packet.source.external_id or packet.source_id,
                canonical_url=packet.source.canonical_url,
                kind=packet.source.kind,
                status=packet.status,
                collections=tuple(collection.name for collection in packet.collections),
                captured_at=packet.captured_at,
                content="\n".join(part for part in parts if part),
            )

    def _artifact_text(self, artifact: ArtifactRef) -> str:
        media_type = artifact.media_type.split(";", 1)[0].strip().lower()
        textual = (
            media_type.startswith("text/")
            or media_type == "application/json"
            or media_type.endswith("+json")
        )
        if not textual or artifact.size_bytes > self._max_bytes:
            return ""
        with self._artifacts.open(artifact.artifact_id) as stream:
            data = stream.read(self._max_bytes + 1)
        if len(data) > self._max_bytes:
            return ""
        return data.decode("utf-8", errors="replace")


class LexicalSearchIndex:
    """Small deterministic baseline; score is relevance, never source trust."""

    def __init__(self, documents: tuple[SearchDocument, ...]) -> None:
        self._documents = documents

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        collection: str | None = None,
        kind: SourceKind | None = None,
        status: CaptureStatus | None = None,
    ) -> tuple[SearchResult, ...]:
        if limit < 1:
            raise ValueError("search limit must be at least 1")
        terms = [term.casefold() for term in _TERM.findall(query)]
        if not terms:
            raise ValueError("search query must contain at least one term")
        collection_fold = collection.casefold() if collection else None

        matches: list[SearchResult] = []
        for document in self._documents:
            if kind is not None and document.kind != kind:
                continue
            if status is not None and document.status != status:
                continue
            if collection_fold is not None and collection_fold not in {
                item.casefold() for item in document.collections
            }:
                continue
            content = document.content.casefold()
            title = document.title.casefold()
            collections = " ".join(document.collections).casefold()
            if any(term not in content for term in terms):
                continue
            score = sum(
                title.count(term) * 5 + collections.count(term) * 3 + content.count(term)
                for term in terms
            )
            matches.append(
                SearchResult(
                    source_id=document.source_id,
                    capture_id=document.capture_id,
                    external_id=document.external_id,
                    title=document.title,
                    canonical_url=document.canonical_url,
                    kind=document.kind,
                    status=document.status,
                    collections=document.collections,
                    captured_at=document.captured_at,
                    score=score,
                    snippet=self._snippet(document.content, terms),
                )
            )
        matches.sort(
            key=lambda result: (
                -result.score,
                -result.captured_at.timestamp(),
                result.capture_id,
            )
        )
        return tuple(matches[:limit])

    @staticmethod
    def _snippet(content: str, terms: list[str], width: int = 240) -> str:
        normalized = " ".join(content.split())
        folded = normalized.casefold()
        positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
        if not positions:
            return normalized[:width]
        center = min(positions)
        start = max(0, center - width // 3)
        end = min(len(normalized), start + width)
        prefix = "…" if start else ""
        suffix = "…" if end < len(normalized) else ""
        return f"{prefix}{normalized[start:end]}{suffix}"
