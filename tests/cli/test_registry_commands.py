from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from sourcefolio import cli
from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemRegistryRouteStore,
    FileSystemRegistrySnapshotStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.frontmatter import parse_frontmatter
from sourcefolio.cli import main
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
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


def add_packet(
    root: Path,
    external_id: str,
    *,
    desc: str = DESCRIPTION,
    captured_at: datetime = NOW,
) -> SourcePacket:
    return (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="tiktok",
                    kind=SourceKind.VIDEO,
                    external_id=external_id,
                    canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
                ),
                captured_at=captured_at,
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
        .packet
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
    assert candidate["status"] == "inbox"
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


def test_candidate_decide_outcome_writes_and_clears(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(build_arguments(tmp_path)) == 0
    capsys.readouterr()
    snapshot = FileSystemRegistrySnapshotStore(tmp_path / "derived").load_latest()
    assert snapshot is not None
    candidate_id = snapshot.candidates[0].candidate_id
    note_path = tmp_path / "vault" / "candidates" / f"{candidate_id}.md"

    assert (
        main(
            decide_arguments(
                tmp_path,
                candidate_id,
                "--status",
                "accepted",
                "--approved-by",
                "mitya",
                "--outcome",
                "пилот запущен",
            )
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    properties = parse_frontmatter(note_path.read_text(encoding="utf-8")).properties

    assert output["applied"]["outcome"] == "пилот запущен"
    assert output["applied"]["outcome_at"] is not None
    assert properties["outcome"] == "пилот запущен"
    assert properties["outcome_at"] is not None

    assert (
        main(
            decide_arguments(
                tmp_path,
                candidate_id,
                "--status",
                "accepted",
                "--approved-by",
                "mitya",
                "--outcome",
                "",
            )
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    properties = parse_frontmatter(note_path.read_text(encoding="utf-8")).properties

    assert output["applied"]["outcome"] is None
    assert output["applied"]["outcome_at"] is None
    assert "outcome" not in properties
    assert "outcome_at" not in properties


def test_candidate_decide_rejects_outcome_for_inbox(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(build_arguments(tmp_path)) == 0
    capsys.readouterr()
    snapshot = FileSystemRegistrySnapshotStore(tmp_path / "derived").load_latest()
    assert snapshot is not None
    candidate_id = snapshot.candidates[0].candidate_id

    with pytest.raises(SystemExit) as excinfo:
        main(
            decide_arguments(
                tmp_path,
                candidate_id,
                "--status",
                "inbox",
                "--outcome",
                "пилот запущен",
            )
        )

    assert excinfo.value.code == 2
    assert "outcome requires a decided status" in capsys.readouterr().err


def test_candidate_decide_rejects_overlong_outcome(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(build_arguments(tmp_path)) == 0
    capsys.readouterr()
    snapshot = FileSystemRegistrySnapshotStore(tmp_path / "derived").load_latest()
    assert snapshot is not None
    candidate_id = snapshot.candidates[0].candidate_id

    with pytest.raises(SystemExit) as excinfo:
        main(
            decide_arguments(
                tmp_path,
                candidate_id,
                "--status",
                "accepted",
                "--approved-by",
                "mitya",
                "--outcome",
                "x" * 2001,
            )
        )

    assert excinfo.value.code == 2
    assert "outcome exceeds 2000 characters" in capsys.readouterr().err
    assert not (tmp_path / "vault" / "candidates").exists()


def test_registry_extract_new_skips_routed_pair_without_model_call(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    prepare(tmp_path, monkeypatch)
    client = FakeChatClient()
    monkeypatch.setattr(cli, "OpenAIChatClient", lambda _config: client)

    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    assert client.calls == 1

    assert main(extract_arguments(tmp_path, "--new")) == 0
    output = json.loads(capsys.readouterr().out)

    assert client.calls == 1
    assert output["routes_processed"] == 0
    assert output["skipped_cached"] == 1
    assert output["results"][0]["status"] == "skipped"


def test_registry_build_reports_freshness_counters(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    unrouted = add_packet(tmp_path / "archive", "two")

    assert main(build_arguments(tmp_path, "--archive-dir", str(tmp_path / "archive"))) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["routes_ignored"] == 0
    assert output["unrouted_captures"] == [unrouted.capture_id]

    assert (
        main(build_arguments(tmp_path, "--archive-dir", str(tmp_path / "missing"), "--dry-run"))
        == 0
    )
    output = json.loads(capsys.readouterr().out)

    assert output["routes_ignored"] == 0
    assert output["unrouted_captures"] == []


def test_registry_queue_links_latest_capture_card(tmp_path: Path, capsys, monkeypatch) -> None:
    prepare(tmp_path, monkeypatch)
    assert main(extract_arguments(tmp_path)) == 0
    capsys.readouterr()
    old = next(FileSystemSourcePacketRepository(tmp_path / "archive").iter_packets())
    latest = add_packet(tmp_path / "archive", "one", captured_at=NOW.replace(minute=1))
    assert main(build_arguments(tmp_path, "--archive-dir", str(tmp_path / "archive"))) == 0
    capsys.readouterr()

    assert main(queue_arguments(tmp_path, "--archive-dir", str(tmp_path / "archive"))) == 0
    capsys.readouterr()

    project_note = (
        tmp_path / "vault" / "_generated" / "sourcefolio" / "registry" / "sourcefolio.md"
    ).read_text(encoding="utf-8")
    assert f"source-cards/{old.source_id}/{latest.capture_id}" in project_note
    assert f"source-cards/{old.source_id}/{old.capture_id}" not in project_note


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
