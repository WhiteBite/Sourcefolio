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
from sourcefolio.cli import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    LlmConfig,
    main,
    resolve_llm_config,
)
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
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
                "title": f"Title {self.calls}",
                "summary": "Summary",
                "category": "ai",
                "entities": [{"type": "tool", "value": "PyTorch"}],
                "links": [],
                "tags": ["нейронки"],
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
            collections=(CollectionMembership(name="Нейронки"),),
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


def test_resolve_llm_config_prefers_flags_then_env_then_file(tmp_path: Path) -> None:
    config_path = tmp_path / "llm.json"
    config_path.write_text(
        json.dumps({"base_url": "file-url", "api_key": "file-key", "model": "file-model"}),
        encoding="utf-8",
    )

    assert resolve_llm_config(config_path=config_path, environ={}) == LlmConfig(
        "file-url", "file-key", "file-model"
    )
    assert resolve_llm_config(
        config_path=config_path,
        environ={
            "EV_LLM_BASE_URL": "env-url",
            "EV_LLM_API_KEY": "env-key",
            "EV_LLM_MODEL": "env-model",
        },
    ) == LlmConfig("env-url", "env-key", "env-model")
    assert resolve_llm_config(
        base_url="flag-url",
        api_key="flag-key",
        model="flag-model",
        config_path=config_path,
        environ={
            "EV_LLM_BASE_URL": "env-url",
            "EV_LLM_API_KEY": "env-key",
            "EV_LLM_MODEL": "env-model",
        },
    ) == LlmConfig("flag-url", "flag-key", "flag-model")


def test_resolve_llm_config_defaults_and_requires_api_key(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    config = resolve_llm_config(api_key="k", config_path=missing, environ={})
    assert config.base_url == DEFAULT_LLM_BASE_URL
    assert config.model == DEFAULT_LLM_MODEL

    with pytest.raises(ValueError, match="api_key is required"):
        resolve_llm_config(config_path=missing, environ={})


def test_llm_enrich_cli_writes_cache_and_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    add_packet(archive, "two")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)
    arguments = [
        "llm-enrich",
        "--collection",
        "Нейронки",
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
    assert first["collection"] == "Нейронки"
    assert first["model"] == "test-model"
    assert (first["processed"], first["skipped_cached"], first["failed"]) == (2, 0, 0)
    assert len(list((derived / "llm").glob("*.json"))) == 2
    note = vault / "_generated" / "sourcefolio" / "llm" / "neyronki.md"
    assert note.is_file()
    assert "Machine inference" in note.read_text(encoding="utf-8")

    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second["processed"], second["skipped_cached"], second["failed"]) == (0, 2, 0)


def test_llm_enrich_cli_dry_run_writes_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    add_packet(archive, "one")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "llm-enrich",
                "--collection",
                "Нейронки",
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
    assert not (derived / "llm").exists()
    assert not vault.exists()


def test_llm_enrich_cli_fails_without_api_key(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("EV_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_LLM_CONFIG_PATH", tmp_path / "missing.json")

    with pytest.raises(SystemExit) as excinfo:
        main(["llm-enrich", "--collection", "Нейронки", "--archive-dir", str(tmp_path)])

    assert excinfo.value.code == 2
    assert "api_key is required" in capsys.readouterr().err
