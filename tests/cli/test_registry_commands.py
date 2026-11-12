from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio import cli
from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemRegistryRouteStore,
    FileSystemRegistrySnapshotStore,
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
DESCRIPTION = "Alpha Tool is a useful library for working with text as data"
CANDIDATE_RESPONSE = {
    "verdict": "candidate",
    "reason": "ok",
    "mentions": [
        {
            "name": "Alpha Tool",
            "kind": "tool",
            "fit": "high",
            "quote": "Alpha Tool",
            "field": "description",
            "comment_index": None,
        }
    ],
}


class FakeChatClient:
    def __init__(self, config: object = None) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return json.dumps(CANDIDATE_RESPONSE, ensure_ascii=False)


class FailingChatClient:
    def __init__(self, config: object = None) -> None:
        pass

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("boom")


def add_packet(root: Path, external_id: str, *, desc: str = DESCRIPTION) -> None:
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
                    {"id": "sourcefolio", "name": "Sourcefolio", "description": "evidence archive"}
                ]
            }
        ),
        encoding="utf-8",
    )


def extract_arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "registry-extract",
        "--collection",
        "Нейронки",
        "--projects",
        str(tmp_path / "projects.json"),
        "--profiles",
        str(tmp_path / "profiles"),
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


def build_arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "registry-build",
        "--projects",
        str(tmp_path / "projects.json"),
        "--profiles",
        str(tmp_path / "profiles"),
        "--derived-dir",
        str(tmp_path / "derived"),
        "--vault",
        str(tmp_path / "vault"),
        *extra,
    ]


def queue_arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "registry-queue",
        "--projects",
        str(tmp_path / "projects.json"),
        "--profiles",
        str(tmp_path / "profiles"),
        "--derived-dir",
        str(tmp_path / "derived"),
        "--vault",
        str(tmp_path / "vault"),
        *extra,
    ]


def decide_arguments(tmp_path: Path, candidate_id: str, *extra: str) -> list[str]:
    return [
        "candidate-decide",
        "--candidate-id",
        candidate_id,
        "--projects",
        str(tmp_path / "projects.json"),
        "--derived-dir",
        str(tmp_path / "derived"),
        "--vault",
        str(tmp_path / "vault"),
        *extra,
    ]


def prepare(tmp_path: Path, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one")
    write_projects(tmp_path / "projects.json")
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)


def test_registry_extract_writes_routes(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)

    assert main(extract_arguments(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["projects"] == ["sourcefolio"]
    assert output["model"] == "test-model"
    assert output["items_scanned"] == 1
    assert output["routes_processed"] == 1
    assert output["skipped_cached"] == 0
    assert output["failed"] == 0
    assert output["verdicts"]["candidate"] == 1
    assert output["mentions"] == 1
    assert output["dropped_anchors"] == 0
    assert output["dry_run"] is False
    assert output["results"][0]["project_id"] == "sourcefolio"
    assert output["results"][0]["status"] == "processed"
    assert output["results"][0]["verdict"] == "candidate"
    assert list(FileSystemRegistryRouteStore(tmp_path / "derived").iter_all())


def test_registry_extract_dry_run_writes_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)

    assert main(extract_arguments(tmp_path, "--dry-run")) == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert output["dry_run"] is True
    assert output["routes_processed"] == 1
    assert "PROMPT 1" in captured.err
    assert list((tmp_path / "derived").rglob("*.json")) == []


def test_registry_build_writes_snapshot(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()

    assert main(build_arguments(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["snapshot_id"].startswith("snap_")
    assert output["snapshot_path"] == f"snapshots/{output['snapshot_id']}.json"
    assert output["candidates"] == 1
    assert output["mentions"] == 1
    assert output["projects"]["sourcefolio"]["candidates"] == 1
    assert output["dry_run"] is False
    snapshot_path = (
        tmp_path / "derived" / "registry" / "snapshots" / f"{output['snapshot_id']}.json"
    )
    assert snapshot_path.is_file()
    assert (tmp_path / "derived" / "registry" / "latest.json").is_file()


def test_registry_queue_prints_queue(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(build_arguments(tmp_path)) == 0
    capsys.readouterr()

    assert main(queue_arguments(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["snapshot_id"].startswith("snap_")
    project = output["projects"][0]
    assert project["project_id"] == "sourcefolio"
    assert project["name"] == "Sourcefolio"
    candidate = project["candidates"][0]
    assert candidate["title"] == "Alpha Tool"
    assert candidate["outcome"] == "inbox"
    registry_root = tmp_path / "vault" / "_generated" / "sourcefolio" / "registry"
    assert (registry_root / "index.md").is_file()


def test_registry_queue_markdown(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(build_arguments(tmp_path)) == 0
    capsys.readouterr()

    assert main(queue_arguments(tmp_path, "--format", "markdown")) == 0

    assert "### Alpha Tool" in capsys.readouterr().out


def test_registry_queue_dry_run_writes_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(build_arguments(tmp_path)) == 0
    capsys.readouterr()

    assert main(queue_arguments(tmp_path, "--dry-run")) == 0

    assert not (tmp_path / "vault" / "_generated" / "sourcefolio" / "registry").exists()


def test_candidate_decide_writes_note(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(build_arguments(tmp_path)) == 0
    capsys.readouterr()
    snapshot = FileSystemRegistrySnapshotStore(tmp_path / "derived").load_latest()
    assert snapshot is not None
    candidate_id = snapshot.candidates[0].candidate_id

    assert (
        main(
            decide_arguments(
                tmp_path,
                candidate_id,
                "--status",
                "accepted",
                "--approved-by",
                "mitya",
                "--rationale",
                "useful",
            )
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)

    assert output["created"] is True
    assert output["applied"]["status"] == "accepted"
    assert output["applied"]["approved_by"] == "mitya"
    assert output["note"] == str(tmp_path / "vault" / "candidates" / f"{candidate_id}.md")
    assert (tmp_path / "vault" / "candidates" / f"{candidate_id}.md").is_file()


def test_candidate_decide_rejects_unknown_id(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)

    assert main(decide_arguments(tmp_path, "cand_" + "0" * 64)) == 1

    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": "unknown candidate_id"}
    assert not (tmp_path / "vault" / "candidates").exists()


def test_registry_extract_fails_when_route_fails(tmp_path: Path, capsys, monkeypatch) -> None:
    add_packet(tmp_path / "archive", "one")
    write_projects(tmp_path / "projects.json")
    monkeypatch.setattr(cli, "OpenAIChatClient", FailingChatClient)

    assert main(extract_arguments(tmp_path)) == 1
    output = json.loads(capsys.readouterr().out)

    assert output["failed"] == 1
    assert output["results"][0]["status"] == "failed"
    assert output["results"][0]["error"] == "boom"
    assert not list(FileSystemRegistryRouteStore(tmp_path / "derived").iter_all())
