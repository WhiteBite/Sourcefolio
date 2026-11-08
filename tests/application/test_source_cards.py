from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
import yaml

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.source_cards import SourceCardProjection
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def add_packet(root: Path, external_id: str, title: str) -> None:
    store = FileSystemArtifactStore(root)
    packets = FileSystemSourcePacketRepository(root)
    use_case = ArchiveCapture(artifacts=store, packets=packets)
    use_case.handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="tiktok",
                kind=SourceKind.VIDEO,
                external_id=external_id,
                canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
            ),
            captured_at=NOW,
            collector=Provenance(
                actor_kind=ActorKind.TOOL,
                actor_id="test",
                method="test",
            ),
            title=title,
            collections=(CollectionMembership(name="Нейронки"),),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain",
                    stream=BytesIO(f"caption {external_id}".encode()),
                ),
            ),
        )
    )


def add_packet_with_saved_index(root: Path, external_id: str, saved_index: dict) -> None:
    store = FileSystemArtifactStore(root)
    packets = FileSystemSourcePacketRepository(root)
    ArchiveCapture(artifacts=store, packets=packets).handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="tiktok",
                kind=SourceKind.VIDEO,
                external_id=external_id,
                canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
            ),
            captured_at=NOW,
            collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
            title="Ролик",
            collections=(CollectionMembership(name="Нейронки"),),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain",
                    stream=BytesIO(b"caption"),
                ),
            ),
            metadata={"saved_index": saved_index},
        )
    )


def _render_single_card(archive: Path, tmp_path: Path) -> str:
    generated_root = tmp_path / "vault" / "_generated" / "evidence-vault"
    projection = SourceCardProjection(
        packets=FileSystemSourcePacketRepository(archive),
        artifacts=FileSystemArtifactStore(archive),
        store=FileSystemProjectionStore(generated_root),
    )
    packet = next(FileSystemSourcePacketRepository(archive).iter_packets())
    return projection.render(packet).content


def test_source_card_renders_saved_index_metadata(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet_with_saved_index(
        archive,
        "1",
        {
            "author": "one",
            "type": "video",
            "post_created_at": "2026-01-01T12:00:00.000Z",
            "desc": "первое видео #нейронки",
            "comments": [
                {
                    "text": "класс",
                    "author": "bob",
                    "likes": 2,
                    "created_at": "2026-01-02T00:00:00.000Z",
                },
                {
                    "text": "спасибо",
                    "author": "ann",
                    "likes": 5,
                    "created_at": "2026-01-03T00:00:00.000Z",
                },
            ],
        },
    )

    content = _render_single_card(archive, tmp_path)
    properties = yaml.safe_load(content.split("---", 2)[1])

    assert properties["author"] == "one"
    assert properties["video_type"] == "video"
    assert properties["post_created_at"] == "2026-01-01T12:00:00.000Z"
    assert "## Description" in content
    assert "первое видео" in content
    assert "## Comments" in content
    assert content.index("спасибо") < content.index("класс")


def test_source_card_is_defensive_without_saved_index(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", "Без метаданных")

    content = _render_single_card(archive, tmp_path)
    properties = yaml.safe_load(content.split("---", 2)[1])

    assert properties["author"] is None
    assert properties["video_type"] is None
    assert properties["post_created_at"] is None
    assert "## Description" not in content
    assert "## Comments" not in content


def test_source_card_handles_null_comment_fields(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet_with_saved_index(
        archive,
        "1",
        {
            "desc": "текст",
            "comments": [{"text": "без лайков", "author": None, "likes": None}],
        },
    )

    content = _render_single_card(archive, tmp_path)

    assert "## Comments" in content
    assert "без лайков" in content


def test_obsidian_projection_is_rebuildable_and_has_yaml_properties(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", "Первый [ролик]")
    add_packet(archive, "2", "Второй ролик")
    artifact_store = FileSystemArtifactStore(archive)
    packets = FileSystemSourcePacketRepository(archive)
    generated_root = tmp_path / "vault" / "_generated" / "evidence-vault"
    store = FileSystemProjectionStore(generated_root)
    projection = SourceCardProjection(
        packets=packets,
        artifacts=artifact_store,
        store=store,
    )
    manual_note = tmp_path / "vault" / "Manual.md"
    manual_note.write_text("do not touch", encoding="utf-8")

    assert projection.rebuild() == 2
    cards = sorted((generated_root / "source-cards").rglob("*.md"))
    assert len(cards) == 2
    content = cards[0].read_text(encoding="utf-8")
    properties = yaml.safe_load(content.split("---", 2)[1])
    assert properties["generated"] is True
    assert properties["schema_version"] == "source-card/v1"
    assert properties["title"] in {"Первый [ролик]", "Второй ролик"}
    assert properties["collections"] == ["Нейронки"]
    assert "file://" in content
    assert "Generated view" in content

    assert projection.rebuild() == 2
    assert len(list((generated_root / "source-cards").rglob("*.md"))) == 2
    assert manual_note.read_text(encoding="utf-8") == "do not touch"


def test_projection_store_removes_only_its_generated_namespace(tmp_path: Path) -> None:
    root = tmp_path / "generated"
    store = FileSystemProjectionStore(root)
    store.replace_namespace(
        "cards",
        [
            GeneratedDocument("a.md", "A"),
            GeneratedDocument("nested/b.md", "B"),
        ],
    )
    outside = tmp_path / "manual.md"
    outside.write_text("manual", encoding="utf-8")

    assert store.replace_namespace("cards", [GeneratedDocument("a.md", "A2")]) == 1
    assert (root / "cards" / "a.md").read_text(encoding="utf-8") == "A2"
    assert not (root / "cards" / "nested" / "b.md").exists()
    assert outside.read_text(encoding="utf-8") == "manual"


def test_projection_store_rejects_path_escape(tmp_path: Path) -> None:
    store = FileSystemProjectionStore(tmp_path / "generated")

    with pytest.raises(ValueError, match="escapes"):
        store.replace_namespace("cards", [GeneratedDocument("../manual.md", "bad")])
