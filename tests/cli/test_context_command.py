from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.cli import main
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def add_packet(root: Path, external_id: str) -> None:
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
            collections=(CollectionMembership(name="Нейронки"),),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain",
                    stream=BytesIO(b"caption"),
                ),
            ),
            metadata={"saved_index": {"desc": "trading bot idea", "author": "author"}},
        )
    )


def write_projects(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "projects": [
                    {"id": "midas", "name": "MidasAI", "description": "trading bot"},
                ]
            }
        ),
        encoding="utf-8",
    )


def test_context_command_json_and_markdown(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    write_projects(tmp_path / "projects.json")

    assert (
        main(
            [
                "decision-new",
                "--vault",
                str(vault),
                "--title",
                "Adopt SQLite",
                "--project",
                "midas",
                "--date",
                "2026-11-08",
            ]
        )
        == 0
    )
    capsys.readouterr()

    base = [
        "context",
        "--vault",
        str(vault),
        "--derived-dir",
        str(derived),
        "--archive-dir",
        str(archive),
        "--projects",
        str(tmp_path / "projects.json"),
    ]

    assert main(base) == 0
    brief = json.loads(capsys.readouterr().out)
    assert brief["stats"]["sources"] == 1
    assert brief["stats"]["collections"] == 1
    assert brief["stats"]["decisions"] == 1
    assert brief["projects"][0]["id"] == "midas"
    assert brief["decisions"][0]["title"] == "Adopt SQLite"
    assert brief["candidates"] == []

    assert main([*base, "--format", "markdown"]) == 0
    markdown = capsys.readouterr().out
    assert "# Session context brief" in markdown
    assert "Adopt SQLite" in markdown


def test_context_command_degrades_on_missing_projects_and_writes_brief(
    tmp_path: Path, capsys
) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")

    assert (
        main(
            [
                "context",
                "--vault",
                str(vault),
                "--derived-dir",
                str(derived),
                "--archive-dir",
                str(archive),
                "--projects",
                str(tmp_path / "missing.json"),
            ]
        )
        == 0
    )
    brief = json.loads(capsys.readouterr().out)
    assert brief["candidates"] == []

    index = vault / "_generated" / "sourcefolio" / "context" / "index.md"
    assert index.is_file()
    assert "# Session context brief" in index.read_text(encoding="utf-8")
