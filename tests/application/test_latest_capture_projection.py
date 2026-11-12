from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.projections import FileSystemProjectionStore
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.collection_index import CollectionIndexProjection
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.obsidian_views import ObsidianOverviewProjection
from sourcefolio.application.source_cards import SourceCardProjection
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

EARLIER = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _capture(
    root: Path, *, captured_at: datetime, title: str, extra_artifacts: int = 0
) -> SourcePacket:
    artifacts = [
        ArtifactPayload(
            role=ArtifactRole.CAPTION_TEXT,
            media_type="text/plain",
            stream=BytesIO(b"caption"),
        )
    ]
    artifacts.extend(
        ArtifactPayload(
            role=ArtifactRole.CAPTION_TEXT,
            media_type="text/plain",
            stream=BytesIO(f"caption-{index}".encode()),
        )
        for index in range(extra_artifacts)
    )
    return (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="tiktok",
                    kind=SourceKind.VIDEO,
                    external_id="1",
                    canonical_url="https://www.tiktok.com/@x/video/1",
                ),
                captured_at=captured_at,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                title=title,
                collections=(CollectionMembership(name="Нейронки"),),
                artifacts=tuple(artifacts),
            )
        )
        .packet
    )


@pytest.mark.parametrize(("lean_title", "rich_title"), [("A", "B"), ("B", "A")])
def test_same_instant_capture_prefers_the_one_carrying_more_artifacts(
    tmp_path: Path, lean_title: str, rich_title: str
) -> None:
    archive = tmp_path / "archive"
    lean = _capture(archive, captured_at=LATER, title=lean_title)
    rich = _capture(archive, captured_at=LATER, title=rich_title, extra_artifacts=2)
    assert lean.source_id == rich.source_id
    assert len(lean.artifacts) < len(rich.artifacts)

    packets = FileSystemSourcePacketRepository(archive)
    latest = latest_capture_per_source(packets.iter_packets())

    assert latest[lean.source_id].capture_id == rich.capture_id


def test_projections_render_only_latest_capture_per_source(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    older = _capture(archive, captured_at=EARLIER, title="Старый")
    newer = _capture(archive, captured_at=LATER, title="Новый")
    assert older.source_id == newer.source_id
    assert older.capture_id != newer.capture_id

    packets = FileSystemSourcePacketRepository(archive)
    assert len(list(packets.iter_packets())) == 2

    generated = tmp_path / "vault" / "_generated" / "sourcefolio"
    store = FileSystemProjectionStore(generated)

    assert (
        SourceCardProjection(
            packets=packets,
            artifacts=FileSystemArtifactStore(archive),
            store=store,
        ).rebuild()
        == 1
    )
    cards = list((generated / "source-cards").rglob("*.md"))
    assert len(cards) == 1
    assert cards[0].name == f"{newer.capture_id}.md"
    assert "Новый" in cards[0].read_text(encoding="utf-8")

    assert CollectionIndexProjection(packets=packets, store=store).rebuild() == 2
    moc = (generated / "collections" / "neyronki.md").read_text(encoding="utf-8")
    assert moc.count("[[_generated/sourcefolio/source-cards/") == 1
    assert "## Videos (1)" in moc
    assert newer.capture_id in moc
    assert older.capture_id not in moc

    assert (
        ObsidianOverviewProjection(packets=packets, store=store, clock=lambda: LATER).rebuild() == 1
    )
    overview = (generated / "overview" / "index.md").read_text(encoding="utf-8")
    assert "capture_count: 1" in overview
    assert newer.capture_id in overview
    assert older.capture_id not in overview
