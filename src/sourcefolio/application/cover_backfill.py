from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.capture_ports import CaptureAdapter
from sourcefolio.application.capture_types import AdapterCapture, CaptureError
from sourcefolio.application.commands import (
    ArchiveCaptureCommand,
    ArchiveCaptureResult,
    ArtifactPayload,
)
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.ports import ArtifactStore, SourcePacketRepository
from sourcefolio.domain.capture import CaptureTask
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CaptureId,
    SourceId,
    SourceKind,
    SourcePacket,
)

_COLLECTOR = Provenance(
    actor_kind=ActorKind.TOOL,
    actor_id="cover-backfill",
    method="cover-backfill",
    version="1",
)
_MAX_REASON_LENGTH = 200


@dataclass(frozen=True)
class CoverBackfillQuery:
    collections: tuple[str, ...] = ()
    kinds: frozenset[SourceKind] = frozenset({SourceKind.VIDEO, SourceKind.SLIDESHOW})
    limit: int | None = None
    skip_existing: bool = True

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")


@dataclass(frozen=True)
class CoverBackfillItemResult:
    source_id: SourceId
    capture_id: CaptureId | None
    status: Literal["created", "skipped", "failed"]
    reason: str | None = None


@dataclass(frozen=True)
class CoverBackfillReport:
    selected: int
    created: int
    skipped: int
    failed: int
    results: tuple[CoverBackfillItemResult, ...]


def _reason(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:_MAX_REASON_LENGTH] or type(exc).__name__


def _collection_names(collections: tuple[str, ...]) -> frozenset[str]:
    return frozenset(name.strip().casefold() for name in collections if name.strip())


class CoverBackfillRunner:
    """Add one preview image to the latest capture of each archived source."""

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactStore,
        archive: ArchiveCapture,
        videos: CaptureAdapter,
        slideshows: CaptureAdapter,
        concurrency: int = 2,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._packets = packets
        self._artifacts = artifacts
        self._archive = archive
        self._videos = videos
        self._slideshows = slideshows
        self._concurrency = concurrency

    async def run(self, query: CoverBackfillQuery) -> CoverBackfillReport:
        selected = self._select(query)
        semaphore = asyncio.Semaphore(self._concurrency)

        async def process(packet: SourcePacket) -> CoverBackfillItemResult:
            async with semaphore:
                return await self._process(packet, query)

        results = await asyncio.gather(*(process(packet) for packet in selected))
        ordered = tuple(sorted(results, key=lambda item: item.source_id))
        return CoverBackfillReport(
            selected=len(ordered),
            created=sum(item.status == "created" for item in ordered),
            skipped=sum(item.status == "skipped" for item in ordered),
            failed=sum(item.status == "failed" for item in ordered),
            results=ordered,
        )

    def _select(self, query: CoverBackfillQuery) -> list[SourcePacket]:
        latest = latest_capture_per_source(self._packets.iter_packets())
        names = _collection_names(query.collections)
        candidates = [
            packet
            for packet in latest.values()
            if packet.source.kind in query.kinds
            and (not names or any(item.name.casefold() in names for item in packet.collections))
        ]
        candidates.sort(key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id))
        if query.limit is not None:
            candidates = candidates[: query.limit]
        return candidates

    async def _process(
        self,
        packet: SourcePacket,
        query: CoverBackfillQuery,
    ) -> CoverBackfillItemResult:
        if query.skip_existing and any(
            artifact.role == ArtifactRole.ORIGINAL_IMAGE for artifact in packet.artifacts
        ):
            return CoverBackfillItemResult(
                source_id=packet.source_id,
                capture_id=None,
                status="skipped",
                reason="cover already present",
            )
        adapter = self._videos if packet.source.kind == SourceKind.VIDEO else self._slideshows
        task = CaptureTask.create(
            source=packet.source,
            raw_entries=({"cover_backfill_from_capture": packet.capture_id},),
            metadata={"cover_backfill": {"from_capture_id": packet.capture_id}},
        )
        try:
            result = await adapter.capture(task)
        except CaptureError as exc:
            return CoverBackfillItemResult(
                source_id=packet.source_id,
                capture_id=None,
                status="failed",
                reason=_reason(exc),
            )
        except Exception as exc:
            return CoverBackfillItemResult(
                source_id=packet.source_id,
                capture_id=None,
                status="failed",
                reason=f"unexpected {type(exc).__name__}",
            )
        try:
            return await self._archive_cover(packet, result)
        finally:
            result.close()

    async def _archive_cover(
        self,
        packet: SourcePacket,
        result: AdapterCapture,
    ) -> CoverBackfillItemResult:
        image = next(
            (
                artifact
                for artifact in result.artifacts
                if artifact.role == ArtifactRole.ORIGINAL_IMAGE
            ),
            None,
        )
        if image is None:
            return CoverBackfillItemResult(
                source_id=packet.source_id,
                capture_id=None,
                status="failed",
                reason="adapter returned no image artifact",
            )
        try:
            archived = await asyncio.to_thread(self._archive_capture, packet, image)
        except Exception as exc:
            return CoverBackfillItemResult(
                source_id=packet.source_id,
                capture_id=None,
                status="failed",
                reason=_reason(exc),
            )
        return CoverBackfillItemResult(
            source_id=archived.packet.source_id,
            capture_id=archived.packet.capture_id,
            status="created" if archived.created else "skipped",
            reason=None if archived.created else "capture already archived",
        )

    def _archive_capture(
        self,
        packet: SourcePacket,
        image: ArtifactPayload,
    ) -> ArchiveCaptureResult:
        opened = []
        payloads: list[ArtifactPayload] = []
        try:
            for ref in packet.artifacts:
                stream = self._artifacts.open(ref.artifact_id)
                opened.append(stream)
                payloads.append(
                    ArtifactPayload(
                        role=ref.role,
                        media_type=ref.media_type,
                        stream=stream,
                        original_filename=ref.original_filename,
                    )
                )
            payloads.append(image)
            metadata = dict(packet.metadata)
            metadata["cover_backfill"] = {"from_capture_id": packet.capture_id}
            return self._archive.handle(
                ArchiveCaptureCommand(
                    source=packet.source,
                    captured_at=packet.captured_at,
                    collector=_COLLECTOR,
                    artifacts=tuple(payloads),
                    status=packet.status,
                    title=packet.title,
                    collections=packet.collections,
                    limitations=packet.limitations,
                    metadata=metadata,
                )
            )
        finally:
            for stream in opened:
                stream.close()
