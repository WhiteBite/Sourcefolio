from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from evidence_vault import cli
from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.cli import main
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeChatClient:
    def __init__(self, config: object) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return json.dumps(
            {
                "title": "The Matrix",
                "year": "1999",
                "kind": "film",
                "confidence": "high",
                "note": "",
            }
        )


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
            collections=(CollectionMembership(name="Фильмы"),),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain",
                    stream=BytesIO(b"caption"),
                ),
            ),
            metadata={"saved_index": {"desc": "текст", "author": "author"}},
        )
    )


def test_llm_catalog_cli_writes_cache_and_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    add_packet(archive, "two")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)
    arguments = [
        "llm-catalog",
        "--collection",
        "Фильмы",
        "--archive-dir",
        str(archive),
        "--derived-dir",
        str(derived),
        "--vault",
        str(vault),
        "--api-key",
        "test-key",
        "--model",
        "test-model",
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["collection"] == "Фильмы"
    assert first["model"] == "test-model"
    assert (first["processed"], first["skipped_cached"], first["failed"]) == (2, 0, 0)
    assert first["catalog_size"] == 1
    assert first["unresolved"] == 0
    assert len(list((derived / "llm-catalog").glob("*.json"))) == 2
    note = vault / "_generated" / "evidence-vault" / "llm-catalog" / "filmy.md"
    assert note.is_file()
    assert "Machine inference" in note.read_text(encoding="utf-8")

    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second["processed"], second["skipped_cached"], second["failed"]) == (0, 2, 0)
    assert second["catalog_size"] == 1


def test_llm_catalog_cli_dry_run_writes_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "llm-catalog",
                "--collection",
                "Фильмы",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
                "--api-key",
                "test-key",
                "--dry-run",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["note_path"] is None
    assert not (derived / "llm-catalog").exists()
    assert not vault.exists()
