from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import yaml

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.collection_index import CollectionIndexProjection
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.slugs import slugify
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def add_packet(
    root: Path,
    external_id: str,
    *,
    collections: tuple[str, ...],
    desc: str,
    title: str | None = None,
) -> None:
    ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    ).handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="tiktok",
                kind=SourceKind.VIDEO,
                external_id=external_id,
                canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
            ),
            captured_at=NOW,
            collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
            title=title,
            collections=tuple(CollectionMembership(name=name) for name in collections),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain",
                    stream=BytesIO(b"caption"),
                ),
            ),
            metadata={"saved_index": {"desc": desc}},
        )
    )


def test_slugify_handles_cyrillic_and_spaces() -> None:
    assert slugify("Нейронки") == "neyronki"
    assert slugify("My Ideas 2026") == "my-ideas-2026"
    assert slugify("!!!").startswith("collection-")


def test_collection_index_builds_moc_with_extracted_entities(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(
        archive,
        "1",
        collections=("Нейронки",),
        desc="repo https://github.com/owner/repo #нейронки",
        title="Первый",
    )
    add_packet(
        archive,
        "2",
        collections=("Нейронки",),
        desc="снова github.com/owner/repo #нейронки #ai",
        title="Второй",
    )
    add_packet(archive, "3", collections=("Идеи",), desc="без ссылок", title="Третий")

    generated = tmp_path / "vault" / "_generated" / "evidence-vault"
    store = FileSystemProjectionStore(generated)
    count = CollectionIndexProjection(
        packets=FileSystemSourcePacketRepository(archive),
        store=store,
    ).rebuild()

    assert count == 3
    moc = (generated / "collections" / "neyronki.md").read_text(encoding="utf-8")
    properties = yaml.safe_load(moc.split("---", 2)[1])
    assert properties["collection"] == "Нейронки"
    assert properties["video_count"] == 2
    assert "## Videos (2)" in moc
    assert "[[_generated/evidence-vault/source-cards/" in moc
    assert "https://github.com/owner/repo" in moc
    assert "#нейронки (2)" in moc
    assert "#ai (1)" in moc

    index = (generated / "collections" / "index.md").read_text(encoding="utf-8")
    assert "Нейронки" in index
    assert "Идеи" in index
    assert "[[_generated/evidence-vault/collections/neyronki|Нейронки]]" in index


def test_collection_index_reports_absence_of_entities(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", collections=("Идеи",), desc="просто текст", title="Один")

    generated = tmp_path / "vault" / "_generated" / "evidence-vault"
    store = FileSystemProjectionStore(generated)
    CollectionIndexProjection(
        packets=FileSystemSourcePacketRepository(archive),
        store=store,
    ).rebuild()

    moc = (generated / "collections" / "idei.md").read_text(encoding="utf-8")
    assert "No repository links found." in moc
    assert "No hashtags found." in moc
