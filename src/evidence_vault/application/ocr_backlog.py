from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from evidence_vault.application.extract_text import ExtractText
from evidence_vault.application.extraction_ports import (
    ExtractionRepository,
    TextExtractionAdapter,
)
from evidence_vault.application.extraction_types import ExtractionError
from evidence_vault.application.metrics import DurationSummary, summarize_durations
from evidence_vault.application.ports import ArtifactStore, SourcePacketRepository
from evidence_vault.domain.extraction import EvidenceLocator, ExtractionRecord, LocatorKind
from evidence_vault.domain.source import (
    ArtifactId,
    ArtifactRef,
    ArtifactRole,
    CaptureId,
    CaptureStatus,
    SourceId,
    SourceKind,
    SourcePacket,
)


@dataclass(frozen=True)
class OcrBacklogQuery:
    capture_limit: int = 10
    slide_limit: int = 100
    capture_id: CaptureId | None = None
    collection: str | None = None
    kind: SourceKind | None = None
    status: CaptureStatus | None = None
    latest_only: bool = True

    def __post_init__(self) -> None:
        if self.capture_limit < 1 or self.slide_limit < 1:
            raise ValueError("capture_limit and slide_limit must be at least 1")


@dataclass(frozen=True)
class OcrBacklogItemResult:
    source_id: SourceId
    capture_id: CaptureId
    artifact_id: ArtifactId
    slide: int
    status: Literal["created", "skipped", "failed"]
    duration_seconds: float
    record: ExtractionRecord | None = None
    error: str | None = None


@dataclass(frozen=True)
class OcrBacklogReport:
    started_at: datetime
    finished_at: datetime
    adapter_prepared: bool
    concurrency: int
    matched_captures: int
    selected_captures: int
    total_images: int
    selected_images: int
    created: int
    skipped: int
    failed: int
    wall_seconds: float
    item_duration: DurationSummary
    results: tuple[OcrBacklogItemResult, ...]


@dataclass(frozen=True)
class _WorkItem:
    packet: SourcePacket
    artifact: ArtifactRef
    slide: int


class OcrBacklogRunner:
    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactStore,
        extractions: ExtractionRepository,
        adapter: TextExtractionAdapter,
        concurrency: int = 2,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if concurrency < 1:
            raise ValueError("OCR concurrency must be at least 1")
        self._packets = packets
        self._adapter = adapter
        self._concurrency = concurrency
        self._monotonic = monotonic
        self._clock = clock or (lambda: datetime.now(UTC))
        self._extract = ExtractText(
            artifacts=artifacts,
            extractions=extractions,
            clock=clock,
        )

    def run(self, query: OcrBacklogQuery) -> OcrBacklogReport:
        started_at = self._clock()
        wall_started = self._monotonic()
        matched, selected, total_images, work = self._select(query)
        if not work:
            return OcrBacklogReport(
                started_at=started_at,
                finished_at=self._clock(),
                adapter_prepared=False,
                concurrency=self._concurrency,
                matched_captures=matched,
                selected_captures=selected,
                total_images=total_images,
                selected_images=0,
                created=0,
                skipped=0,
                failed=0,
                wall_seconds=self._monotonic() - wall_started,
                item_duration=summarize_durations(()),
                results=(),
            )

        self._adapter.prepare()
        results: list[OcrBacklogItemResult] = []
        chunk_size = self._concurrency * 2
        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            for start in range(0, len(work), chunk_size):
                chunk = work[start : start + chunk_size]
                results.extend(pool.map(self._execute, chunk))
        return OcrBacklogReport(
            started_at=started_at,
            finished_at=self._clock(),
            adapter_prepared=True,
            concurrency=self._concurrency,
            matched_captures=matched,
            selected_captures=selected,
            total_images=total_images,
            selected_images=len(work),
            created=sum(item.status == "created" for item in results),
            skipped=sum(item.status == "skipped" for item in results),
            failed=sum(item.status == "failed" for item in results),
            wall_seconds=self._monotonic() - wall_started,
            item_duration=summarize_durations(item.duration_seconds for item in results),
            results=tuple(results),
        )

    def _select(
        self,
        query: OcrBacklogQuery,
    ) -> tuple[int, int, int, list[_WorkItem]]:
        candidates: dict[CaptureId, SourcePacket] = {}
        latest_by_source: dict[SourceId, SourcePacket] = {}
        collection = query.collection.casefold() if query.collection else None
        for packet in self._packets.iter_packets():
            if query.capture_id is not None and packet.capture_id != query.capture_id:
                continue
            if query.kind is not None and packet.source.kind != query.kind:
                continue
            if query.status is not None and packet.status != query.status:
                continue
            if collection is not None and collection not in {
                item.name.casefold() for item in packet.collections
            }:
                continue
            if not any(
                artifact.role == ArtifactRole.ORIGINAL_IMAGE for artifact in packet.artifacts
            ):
                continue
            if query.latest_only and query.capture_id is None:
                previous = latest_by_source.get(packet.source_id)
                if previous is None or (packet.captured_at, packet.capture_id) > (
                    previous.captured_at,
                    previous.capture_id,
                ):
                    latest_by_source[packet.source_id] = packet
            else:
                candidates[packet.capture_id] = packet

        if query.latest_only and query.capture_id is None:
            candidates = {packet.capture_id: packet for packet in latest_by_source.values()}
        ordered = sorted(
            candidates.values(),
            key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id),
        )
        matched = len(ordered)
        selected_packets = ordered[: query.capture_limit]
        total_images = sum(
            sum(artifact.role == ArtifactRole.ORIGINAL_IMAGE for artifact in packet.artifacts)
            for packet in selected_packets
        )
        work: list[_WorkItem] = []
        for packet in selected_packets:
            images = [
                artifact
                for artifact in packet.artifacts
                if artifact.role == ArtifactRole.ORIGINAL_IMAGE
            ]
            for slide, artifact in enumerate(images, start=1):
                if len(work) >= query.slide_limit:
                    break
                work.append(_WorkItem(packet=packet, artifact=artifact, slide=slide))
            if len(work) >= query.slide_limit:
                break
        return matched, len(selected_packets), total_images, work

    def _execute(self, item: _WorkItem) -> OcrBacklogItemResult:
        started = self._monotonic()
        try:
            result = self._extract.handle(
                packet=item.packet,
                artifact=item.artifact,
                locator=EvidenceLocator(kind=LocatorKind.SLIDE, slide=item.slide),
                adapter=self._adapter,
            )
        except Exception as exc:
            message = str(exc) if isinstance(exc, ExtractionError) else type(exc).__name__
            return OcrBacklogItemResult(
                source_id=item.packet.source_id,
                capture_id=item.packet.capture_id,
                artifact_id=item.artifact.artifact_id,
                slide=item.slide,
                status="failed",
                duration_seconds=self._monotonic() - started,
                error=message,
            )
        return OcrBacklogItemResult(
            source_id=item.packet.source_id,
            capture_id=item.packet.capture_id,
            artifact_id=item.artifact.artifact_id,
            slide=item.slide,
            status="created" if result.created else "skipped",
            duration_seconds=self._monotonic() - started,
            record=result.record,
        )
