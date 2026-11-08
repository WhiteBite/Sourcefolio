from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

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


class FakeGitHubRepoClient:
    instances: list[FakeGitHubRepoClient] = []

    def __init__(self, config: object = None) -> None:
        self.config = config
        self.calls: list[tuple[str, str]] = []
        FakeGitHubRepoClient.instances.append(self)

    def fetch(self, owner: str, repository: str) -> dict[str, object]:
        self.calls.append((owner, repository))
        if repository == "boom":
            raise RuntimeError("GitHub API refused the request (status 403): rate limit reached")
        name = f"{owner}/{repository}"
        return {
            "full_name": name,
            "html_url": f"https://github.com/{name}",
            "description": "desc",
            "stars": 10,
            "language": "Python",
            "license": "MIT",
            "archived": False,
            "pushed_at": "2026-11-01T10:00:00Z",
            "topics": [],
        }


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeGitHubRepoClient.instances.clear()


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


def arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "repo-catalog",
        "--collection",
        "Нейронки",
        "--archive-dir",
        str(tmp_path / "archive"),
        "--derived-dir",
        str(tmp_path / "derived"),
        "--vault",
        str(tmp_path / "vault"),
        *extra,
    ]


def test_repo_catalog_cli_writes_cache_and_note(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "one", desc="https://github.com/owner/repo.git")
    add_packet(archive, "two", desc="see github.com/owner/repo")
    monkeypatch.setattr(cli, "GitHubRepoClient", FakeGitHubRepoClient)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    argv = arguments(tmp_path, "--token", "t0ken")

    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["collection"] == "Нейронки"
    assert first["repos_found"] == 1
    assert (first["fetched"], first["skipped_cached"], first["failed"]) == (1, 0, 0)
    assert first["skipped_non_github"] == 0
    assert first["note_path"].endswith("neyronki.md")
    assert FakeGitHubRepoClient.instances[-1].config.token == "t0ken"
    assert len(list((tmp_path / "derived" / "repos").glob("*.json"))) == 1

    note = tmp_path / "vault" / "_generated" / "evidence-vault" / "repos" / "neyronki.md"
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "provenance: external_observation" in content
    assert content.count("[[_generated/evidence-vault/source-cards/") == 2

    assert main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second["fetched"], second["skipped_cached"], second["failed"]) == (0, 1, 0)
    total_calls = sum(len(client.calls) for client in FakeGitHubRepoClient.instances)
    assert total_calls == 1


def test_repo_catalog_cli_uses_env_token(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one", desc="https://github.com/owner/repo")
    monkeypatch.setattr(cli, "GitHubRepoClient", FakeGitHubRepoClient)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    assert main(arguments(tmp_path)) == 0
    capsys.readouterr()
    assert FakeGitHubRepoClient.instances[-1].config.token == "env-token"


def test_repo_catalog_cli_records_failed_repo(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one", desc="https://github.com/owner/boom")
    monkeypatch.setattr(cli, "GitHubRepoClient", FakeGitHubRepoClient)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert main(arguments(tmp_path)) == 1
    output = json.loads(capsys.readouterr().out)
    assert (output["repos_found"], output["fetched"], output["failed"]) == (1, 0, 1)
    assert output["results"][0]["status"] == "failed"
    assert "rate limit" in output["results"][0]["error"]
    note = tmp_path / "vault" / "_generated" / "evidence-vault" / "repos" / "neyronki.md"
    assert note.is_file()
    assert "## Failed (1)" in note.read_text(encoding="utf-8")


def test_repo_catalog_cli_dry_run_writes_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one", desc="https://github.com/owner/repo")
    monkeypatch.setattr(cli, "GitHubRepoClient", FakeGitHubRepoClient)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert main(arguments(tmp_path, "--dry-run")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["note_path"] is None
    assert FakeGitHubRepoClient.instances[-1].calls == []
    assert not (tmp_path / "derived" / "repos").exists()
    assert not (tmp_path / "vault").exists()
