from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemHumanNoteStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.note import NoteSetter
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def add_packet(root: Path, external_id: str, title: str) -> str:
    result = ArchiveCapture(
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
            collector=Provenance(
                actor_kind=ActorKind.TOOL,
                actor_id="yt-dlp",
                method="download",
                version="2026.1",
            ),
            title=title,
            collections=(CollectionMembership(name="Нейронки"),),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain",
                    stream=BytesIO(f"payload {external_id}".encode()),
                ),
            ),
        )
    )
    return result.packet.source_id


def build_setter(archive: Path, ideas: Path) -> NoteSetter:
    return NoteSetter(
        packets=FileSystemSourcePacketRepository(archive),
        notes=FileSystemHumanNoteStore(ideas),
    )


def test_set_creates_note_when_absent(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    ideas = tmp_path / "ideas"
    source_id = add_packet(archive, "1", "Про агентов")

    result = build_setter(archive, ideas).set(
        source_id, my_title="Агенты", why_saved="для статьи", status="keep"
    )

    assert result is not None
    assert result.created is True
    assert result.applied == {
        "my_title": "Агенты",
        "why_saved": "для статьи",
        "status": "keep",
    }
    content = (ideas / f"{source_id}.md").read_text(encoding="utf-8")
    assert "my_title: Агенты" in content
    assert "why_saved: для статьи" in content
    assert "status: keep" in content
    assert "## Заметки" in content


def test_set_preserves_body_and_unknown_keys(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    ideas = tmp_path / "ideas"
    source_id = add_packet(archive, "1", "Ролик")
    notes = FileSystemHumanNoteStore(ideas)
    original = (
        "---\n"
        "status: inbox\n"
        f"source_id: {source_id}\n"
        "custom_key: stays\n"
        "---\n"
        "\n"
        "## Заметки\n"
        "\n"
        "own line\n"
    )
    assert notes.create_if_absent(f"{source_id}.md", original)

    result = build_setter(archive, ideas).set(source_id, my_title="X")

    assert result is not None
    assert result.created is False
    content = (ideas / f"{source_id}.md").read_text(encoding="utf-8")
    assert "custom_key: stays" in content
    assert "my_title: X" in content
    assert "own line" in content
    assert content.endswith("own line\n")


def test_set_clear_removes_field(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    ideas = tmp_path / "ideas"
    source_id = add_packet(archive, "1", "Ролик")
    setter = build_setter(archive, ideas)
    assert setter.set(source_id, my_title="X") is not None

    result = setter.set(source_id, clear=("my_title",))

    assert result is not None
    assert result.applied == {"my_title": None}
    content = (ideas / f"{source_id}.md").read_text(encoding="utf-8")
    assert "my_title" not in content


def test_set_unknown_source_returns_none(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", "Ролик")

    result = build_setter(archive, tmp_path / "ideas").set("src_" + "0" * 64, my_title="x")

    assert result is None


def test_set_without_fields_raises(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    source_id = add_packet(archive, "1", "Ролик")

    with pytest.raises(ValueError):
        build_setter(archive, tmp_path / "ideas").set(source_id)


def test_set_status_is_not_clearable(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    source_id = add_packet(archive, "1", "Ролик")

    with pytest.raises(ValueError):
        build_setter(archive, tmp_path / "ideas").set(source_id, clear=("status",))
