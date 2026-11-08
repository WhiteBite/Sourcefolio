from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.saved_index import saved_index
from evidence_vault.application.slides_fetch import FetchSlidesQuery, FetchSlidesRunner
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CaptureStatus,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeImageFetcher:
    def __init__(self, payloads: dict[str, bytes], fail: set[str] | None = None) -> None:
        self._payloads = payloads
        self._fail = fail or set()
        self.calls: list[str] = []

    def fetch(self, url: str) -> BytesIO:
        self.calls.append(url)
        if url in self._fail:
            raise RuntimeError("image download failed")
        return BytesIO(self._payloads[url])


def add_packet(
    root: Path,
    external_id: str,
    *,
    collection: str = "Нейронки",
    kind: SourceKind = SourceKind.SLIDESHOW,
    item_type: str = "slideshow",
    images: list[str] | None = None,
    desc: str = "текст",
    captured_at: datetime = NOW,
) -> SourcePacket:
    return (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="tiktok",
                    kind=kind,
                    external_id=external_id,
                    canonical_url=f"https://www.tiktok.com/@x/photo/{external_id}",
                ),
                captured_at=captured_at,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                collections=(CollectionMembership(name=collection),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption"),
                    ),
                ),
                metadata={
                    "saved_index": {
                        "type": item_type,
                        "images": images or [],
                        "author": "author",
                        "desc": desc,
                        "comments": [{"text": "hi", "author": "c", "likes": 1}],
                    }
                },
            )
        )
        .packet
    )


def runner(root: Path, fetcher: FakeImageFetcher) -> FetchSlidesRunner:
    packets = FileSystemSourcePacketRepository(root)
    return FetchSlidesRunner(
        packets=packets,
        archive=ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=packets,
        ),
        fetcher=fetcher,
    )


def fetched_packets(root: Path) -> list[SourcePacket]:
    return [
        packet
        for packet in FileSystemSourcePacketRepository(root).iter_packets()
        if packet.collector.actor_id == "slides-image-fetcher"
    ]


def image_artifacts(packet: SourcePacket) -> list[str]:
    return [
        artifact.sha256
        for artifact in packet.artifacts
        if artifact.role == ArtifactRole.ORIGINAL_IMAGE
    ]


def test_fetch_slides_archives_ordered_images_and_metadata(tmp_path: Path) -> None:
    urls = [
        "https://cdn.example/1.jpeg",
        "https://cdn.example/2.jpeg",
        "https://cdn.example/3.jpeg",
    ]
    add_packet(tmp_path, "one", images=urls)
    fetcher = FakeImageFetcher(
        {url: f"image-{index}".encode() for index, url in enumerate(urls, start=1)}
    )

    report = runner(tmp_path, fetcher).run(FetchSlidesQuery(collection="Нейронки"))

    assert report.sources == 1
    assert report.images_downloaded == 3
    assert report.failed == 0
    assert (report.created, report.skipped) == (1, 0)
    packets = fetched_packets(tmp_path)
    assert len(packets) == 1
    packet = packets[0]
    assert packet.status == CaptureStatus.COMPLETE
    assert image_artifacts(packet) == [
        hashlib.sha256(f"image-{index}".encode()).hexdigest() for index in (1, 2, 3)
    ]
    saved = saved_index(packet)
    assert saved["type"] == "slideshow"
    assert saved["desc"] == "текст"
    assert saved["author"] == "author"
    assert saved["images"] == urls
    assert saved["comments"] == [{"text": "hi", "author": "c", "likes": 1}]


def test_fetch_slides_partial_when_image_fails(tmp_path: Path) -> None:
    urls = [
        "https://cdn.example/1.jpeg",
        "https://cdn.example/2.jpeg",
        "https://cdn.example/3.jpeg",
    ]
    add_packet(tmp_path, "one", images=urls)
    fetcher = FakeImageFetcher(
        {url: b"data" for url in urls},
        fail={"https://cdn.example/2.jpeg"},
    )

    report = runner(tmp_path, fetcher).run(FetchSlidesQuery(collection="Нейронки"))

    assert report.failed == 1
    assert report.images_downloaded == 2
    assert report.created == 1
    packet = fetched_packets(tmp_path)[0]
    assert packet.status == CaptureStatus.PARTIAL
    assert any("2" in limitation for limitation in packet.limitations)
    assert len(image_artifacts(packet)) == 2


def test_fetch_slides_rerun_does_not_duplicate(tmp_path: Path) -> None:
    urls = ["https://cdn.example/1.jpeg", "https://cdn.example/2.jpeg"]
    add_packet(tmp_path, "one", images=urls)
    payloads = {url: b"data" for url in urls}

    first = runner(tmp_path, FakeImageFetcher(payloads)).run(
        FetchSlidesQuery(collection="Нейронки")
    )
    second = runner(tmp_path, FakeImageFetcher(payloads)).run(
        FetchSlidesQuery(collection="Нейронки")
    )

    assert (first.created, first.skipped) == (1, 0)
    assert (second.created, second.skipped) == (0, 1)
    assert len(fetched_packets(tmp_path)) == 1


def test_fetch_slides_ignores_non_slideshow_sources(tmp_path: Path) -> None:
    add_packet(tmp_path, "video", kind=SourceKind.VIDEO, item_type="video", images=[])
    add_packet(tmp_path, "slides", images=["https://cdn.example/1.jpeg"])
    fetcher = FakeImageFetcher({"https://cdn.example/1.jpeg": b"data"})

    report = runner(tmp_path, fetcher).run(FetchSlidesQuery(collection="Нейронки"))

    assert report.sources == 1
    assert fetcher.calls == ["https://cdn.example/1.jpeg"]


def test_fetch_slides_ignores_other_collections(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", collection="Другое", images=["https://cdn.example/1.jpeg"])
    fetcher = FakeImageFetcher({"https://cdn.example/1.jpeg": b"data"})

    report = runner(tmp_path, fetcher).run(FetchSlidesQuery(collection="Нейронки"))

    assert report.sources == 0
    assert fetcher.calls == []


def test_fetch_slides_respects_max_slides_budget(tmp_path: Path) -> None:
    urls = [
        "https://cdn.example/1.jpeg",
        "https://cdn.example/2.jpeg",
        "https://cdn.example/3.jpeg",
    ]
    add_packet(tmp_path, "one", images=urls)
    fetcher = FakeImageFetcher({url: b"data" for url in urls})

    report = runner(tmp_path, fetcher).run(FetchSlidesQuery(collection="Нейронки", max_slides=2))

    assert report.images_downloaded == 2
    assert fetcher.calls == urls[:2]
    packet = fetched_packets(tmp_path)[0]
    assert packet.status == CaptureStatus.PARTIAL
    assert any("3" in limitation for limitation in packet.limitations)
