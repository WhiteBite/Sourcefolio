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
    def __init__(self, config: object, *, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self._fail:
            raise RuntimeError("model unavailable")
        return "Grounded answer citing [1] src_0000."


def add_packet(root: Path, external_id: str, desc: str = "trading bot idea") -> None:
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


def _answers(vault: Path) -> list[Path]:
    return sorted((vault / "_generated" / "evidence-vault" / "answers").glob("*.md"))


def test_ask_no_llm_retrieval_only_writes_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.delenv("EV_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_LLM_CONFIG_PATH", tmp_path / "missing.json")

    assert (
        main(
            [
                "ask",
                "trading bot",
                "--no-llm",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "retrieval_only"
    assert output["grounded"] is False
    assert len(_answers(vault)) == 1


def test_ask_second_run_creates_new_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.delenv("EV_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_LLM_CONFIG_PATH", tmp_path / "missing.json")
    arguments = [
        "ask",
        "trading bot",
        "--no-llm",
        "--archive-dir",
        str(archive),
        "--derived-dir",
        str(derived),
        "--vault",
        str(vault),
    ]

    assert main(arguments) == 0
    capsys.readouterr()
    assert main(arguments) == 0
    capsys.readouterr()
    assert len(_answers(vault)) == 2


def test_ask_llm_success_grounds_answer(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "ask",
                "trading bot",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
                "--api-key",
                "test-key",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["grounded"] is True
    assert output["status"] == "ok"
    note = _answers(vault)[0]
    assert "agent_inference" in note.read_text(encoding="utf-8")


def test_ask_llm_failure_degrades_and_writes_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.setattr(
        cli,
        "OpenAIChatClient",
        lambda config: FakeChatClient(config, fail=True),
    )

    assert (
        main(
            [
                "ask",
                "trading bot",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
                "--api-key",
                "test-key",
            ]
        )
        == 1
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "degraded"
    assert any("LLM synthesis failed" in item for item in output["limitations"])
    assert len(_answers(vault)) == 1


def test_ask_markdown_format_prints_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "ask",
                "trading bot",
                "--format",
                "markdown",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
                "--api-key",
                "test-key",
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert printed.startswith("---")
    assert "Machine inference" in printed


def test_ask_missing_api_key_degrades_and_writes_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.delenv("EV_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_LLM_CONFIG_PATH", tmp_path / "missing.json")

    assert (
        main(
            [
                "ask",
                "trading bot",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
            ]
        )
        == 1
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "degraded"
    assert any("LLM synthesis failed" in item for item in output["limitations"])
    assert len(_answers(vault)) == 1
