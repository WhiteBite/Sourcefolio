from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from sourcefolio import cli
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


class FakeChatClient:
    def __init__(self, config: object = None) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return json.dumps(
            {
                "relevant": True,
                "summary": "useful",
                "matches": [
                    {
                        "project_id": "midas",
                        "fit": "high",
                        "feature": "backtest engine",
                        "why": "trading",
                    }
                ],
            }
        )


def add_packet(root: Path, external_id: str, *, desc: str = "текст") -> None:
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
            metadata={"saved_index": {"desc": desc, "author": "author"}},
        )
    )


def write_projects(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "projects": [
                    {"id": "midas", "name": "MidasAI", "description": "trading bot"},
                    {"id": "mafiin", "name": "Mafiin", "description": "party game"},
                ]
            }
        ),
        encoding="utf-8",
    )


def arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "project-fit",
        "--collection",
        "Нейронки",
        "--projects",
        str(tmp_path / "projects.json"),
        "--archive-dir",
        str(tmp_path / "archive"),
        "--derived-dir",
        str(tmp_path / "derived"),
        "--vault",
        str(tmp_path / "vault"),
        "--api-key",
        "test-key",
        "--model",
        "test-model",
        *extra,
    ]


def test_project_fit_cli_writes_notes(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one", desc="trading bot idea")
    write_projects(tmp_path / "projects.json")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert main(arguments(tmp_path)) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["collections"] == ["Нейронки"]
    assert first["model"] == "test-model"
    assert first["items_scanned"] == 1
    assert (first["processed"], first["skipped_cached"], first["failed"]) == (1, 0, 0)
    assert first["unmatched"] == 0
    assert first["per_project"] == {"midas": 1, "mafiin": 0}
    assert len(first["note_paths"]) == 3
    cache_files = [
        path
        for path in (tmp_path / "derived" / "project-fit").glob("*.json")
        if path.name != "index.json"
    ]
    assert len(cache_files) == 1

    note = tmp_path / "vault" / "_generated" / "sourcefolio" / "projects" / "midasai.md"
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "backtest engine" in content
    assert "provenance: agent_inference" in content

    assert main(arguments(tmp_path)) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second["processed"], second["skipped_cached"], second["failed"]) == (0, 1, 0)


def test_project_fit_cli_dry_run_writes_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one", desc="trading bot idea")
    write_projects(tmp_path / "projects.json")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert main(arguments(tmp_path, "--dry-run")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["note_paths"] == []
    assert not (tmp_path / "derived" / "project-fit").exists()
    assert not (tmp_path / "vault").exists()


def test_project_fit_cli_fails_on_missing_projects(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "project-fit",
                "--collection",
                "Нейронки",
                "--projects",
                str(tmp_path / "missing.json"),
                "--archive-dir",
                str(tmp_path),
                "--api-key",
                "test-key",
            ]
        )

    assert excinfo.value.code == 2
    assert "projects file not found" in capsys.readouterr().err


def test_project_fit_cli_profiles_reach_prompt(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one", desc="trading bot idea")
    write_projects(tmp_path / "projects.json")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "midas.md").write_text("PROFILE-MARKER-12345", encoding="utf-8")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert main(arguments(tmp_path, "--profiles", str(profiles), "--dry-run")) == 0
    captured = capsys.readouterr()
    assert "PROFILE-MARKER-12345" in captured.err


def test_project_fit_cli_profiles_strip_frontmatter(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one", desc="trading bot idea")
    write_projects(tmp_path / "projects.json")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "midas.md").write_text(
        "---\nid: midas\nstatus: draft\ntype: project\n---\n\nPROFILE-BODY-MARKER\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert main(arguments(tmp_path, "--profiles", str(profiles), "--dry-run")) == 0
    err = capsys.readouterr().err
    prompt = err.split("--- preview 1 user ---\n", 1)[1]
    assert "PROFILE-BODY-MARKER" in prompt
    assert "status: draft" not in prompt
    assert "---" not in prompt
