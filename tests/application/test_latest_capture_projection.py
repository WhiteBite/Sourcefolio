from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.collection_index import CollectionIndexProjection
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.obsidian_views import ObsidianOverviewProjection
from evidence_vault.application.source_cards import SourceCardProjection
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

EARLIER = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _capture(root: Path, *, captured_at: datetime, title: str) -> SourcePacket:
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
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption"),
                    ),
                ),
            )
        )
        .packet
    )


def test_projections_render_only_latest_capture_per_source(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    older = _capture(archive, captured_at=EARLIER, title="Старый")
    newer = _capture(archive, captured_at=LATER, title="Новый")
    assert older.source_id == newer.source_id
    assert older.capture_id != newer.capture_id

    packets = FileSystemSourcePacketRepository(archive)
    assert len(list(packets.iter_packets())) == 2

    generated = tmp_path / "vault" / "_generated" / "evidence-vault"
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
    assert moc.count("[[_generated/evidence-vault/source-cards/") == 1
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
