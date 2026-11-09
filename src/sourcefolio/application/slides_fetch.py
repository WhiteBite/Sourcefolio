from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO, Literal, Protocol
from urllib.parse import urlsplit

from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.ports import SourcePacketRepository
from sourcefolio.application.saved_index import saved_index, saved_str
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CaptureId,
    CaptureStatus,
    SourceId,
    SourcePacket,
)

_ACTOR_ID = "slides-image-fetcher"
_COLLECTOR = Provenance(
    actor_kind=ActorKind.TOOL,
    actor_id=_ACTOR_ID,
    method="urllib-image-download",
    version="1",
)
_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class ImageFetcher(Protocol):
    def fetch(self, url: str) -> IO[bytes]:
        """Open a bounded byte stream for one image URL; the caller closes it."""
        ...


@dataclass(frozen=True)
class FetchSlidesQuery:
    collection: str
    limit: int | None = None
    max_slides: int = 50

    def __post_init__(self) -> None:
        if not self.collection.strip():
            raise ValueError("collection name is required")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.max_slides < 1:
            raise ValueError("max_slides must be at least 1")


@dataclass(frozen=True)
class FetchSlidesItemResult:
    source_id: SourceId
    capture_id: CaptureId | None
    status: Literal["created", "skipped", "failed"]
    images_downloaded: int
    failed_indexes: tuple[int, ...]


@dataclass(frozen=True)
class FetchSlidesReport:
    collection: str
    sources: int
    images_downloaded: int
    failed: int
    created: int
    skipped: int
    results: tuple[FetchSlidesItemResult, ...]


def _suffix(url: str) -> str:
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    _, dot, extension = name.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def _media_type(url: str) -> str:
    return _IMAGE_MEDIA_TYPES.get(_suffix(url), "image/jpeg")


def _filename(index: int, url: str) -> str:
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return name if name else f"slide-{index:02}.jpg"


def _image_entries(packet: SourcePacket) -> tuple[tuple[int, str], ...]:
    images = saved_index(packet).get("images")
    if not isinstance(images, list):
        return ()
    return tuple(
        (index, item.strip())
        for index, item in enumerate(images, start=1)
        if isinstance(item, str) and item.strip()
    )


class FetchSlidesRunner:
    """Download slideshow images for a collection and archive them as image artifacts."""

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        archive: ArchiveCapture,
        fetcher: ImageFetcher,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._archive = archive
        self._fetcher = fetcher
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, query: FetchSlidesQuery) -> FetchSlidesReport:
        latest = latest_capture_per_source(self._packets.iter_packets())
        selected = self._select(query, latest)
        results: list[FetchSlidesItemResult] = []
        remaining = query.max_slides
        for packet in selected:
            if remaining <= 0:
                break
            result = self._process(packet, remaining)
            remaining -= result.images_downloaded
            results.append(result)
        return FetchSlidesReport(
            collection=query.collection,
            sources=len(selected),
            images_downloaded=sum(item.images_downloaded for item in results),
            failed=sum(len(item.failed_indexes) for item in results),
            created=sum(item.status == "created" for item in results),
            skipped=sum(item.status == "skipped" for item in results),
            results=tuple(results),
        )

    def _select(
        self,
        query: FetchSlidesQuery,
        latest: dict[SourceId, SourcePacket],
    ) -> list[SourcePacket]:
        collection = query.collection.casefold()
        selected = [
            packet
            for packet in latest.values()
            if collection in {item.name.casefold() for item in packet.collections}
            and saved_str(saved_index(packet), "type") == "slideshow"
            and _image_entries(packet)
        ]
        selected.sort(key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id))
        if query.limit is not None:
            selected = selected[: query.limit]
        return selected

    def _process(self, packet: SourcePacket, remaining: int) -> FetchSlidesItemResult:
        saved = saved_index(packet)
        entries = _image_entries(packet)
        allowed = entries[:remaining]
        budget_skipped = tuple(index for index, _ in entries[remaining:])
        streams: list[tuple[int, str, IO[bytes]]] = []
        failed: list[int] = []
        for index, url in allowed:
            try:
                streams.append((index, url, self._fetcher.fetch(url)))
            except Exception:
                failed.append(index)
        if not streams:
            return FetchSlidesItemResult(
                source_id=packet.source_id,
                capture_id=None,
                status="failed",
                images_downloaded=0,
                failed_indexes=tuple(failed),
            )
        limitations: list[str] = []
        if failed:
            limitations.append(f"failed to download image indexes: {', '.join(map(str, failed))}")
        if budget_skipped:
            limitations.append(
                "max-slides budget reached; skipped image indexes: "
                f"{', '.join(map(str, budget_skipped))}"
            )
        payloads = tuple(
            ArtifactPayload(
                role=ArtifactRole.ORIGINAL_IMAGE,
                media_type=_media_type(url),
                stream=stream,
                original_filename=_filename(index, url),
            )
            for index, url, stream in streams
        )
        try:
            result = self._archive.handle(
                ArchiveCaptureCommand(
                    source=packet.source,
                    captured_at=packet.captured_at,
                    collector=_COLLECTOR,
                    artifacts=payloads,
                    status=CaptureStatus.COMPLETE if not limitations else CaptureStatus.PARTIAL,
                    title=saved_str(saved, "desc"),
                    collections=packet.collections,
                    limitations=tuple(limitations),
                    metadata={"saved_index": dict(saved)},
                )
            )
        finally:
            for _, _, stream in streams:
                stream.close()
        return FetchSlidesItemResult(
            source_id=result.packet.source_id,
            capture_id=result.packet.capture_id,
            status="created" if result.created else "skipped",
            images_downloaded=len(streams),
            failed_indexes=tuple(failed),
        )
