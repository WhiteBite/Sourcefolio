from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio import cli
from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.capture_types import AdapterCapture
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


class FakeCoverAdapter:
    calls: list[str] = []
    image: bytes | None = b"cover-bytes"

    def __init__(self, config: object = None) -> None:
        self.config = config

    @property
    def operation_key(self) -> str:
        return "fake-cover/v1"

    async def capture(self, task) -> AdapterCapture:
        FakeCoverAdapter.calls.append(task.source.external_id or "")
        artifacts = ()
        if FakeCoverAdapter.image is not None:
            artifacts = (
                ArtifactPayload(
                    role=ArtifactRole.ORIGINAL_IMAGE,
                    media_type="image/jpeg",
                    stream=BytesIO(FakeCoverAdapter.image),
                    original_filename="cover.jpg",
                ),
            )
        return AdapterCapture(
            source=task.source,
            captured_at=NOW,
            artifacts=artifacts,
            limitations=("cover-only capture: video was not downloaded",),
            satisfied=False,
        )


def add_video(root: Path, external_id: str, collection: str = "Нейронки") -> None:
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
            collections=(CollectionMembership(name=collection),),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.ORIGINAL_VIDEO,
                    media_type="video/mp4",
                    stream=BytesIO(b"video-bytes"),
                ),
            ),
        )
    )


def arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "fetch-covers",
        "--archive-dir",
        str(tmp_path / "archive"),
        "--temp-dir",
        str(tmp_path / "tmp"),
        *extra,
    ]


def test_fetch_covers_cli_creates_cover_capture(tmp_path: Path, capsys, monkeypatch) -> None:
    add_video(tmp_path / "archive", "one")
    FakeCoverAdapter.calls = []
    FakeCoverAdapter.image = b"cover-bytes"
    monkeypatch.setattr(cli, "YtDlpTikTokAdapter", FakeCoverAdapter)
    monkeypatch.setattr(cli, "GalleryDlTikTokSlidesAdapter", FakeCoverAdapter)

    assert main(arguments(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["selected"] == 1
    assert (output["created"], output["skipped"], output["failed"]) == (1, 0, 0)
    assert output["results"][0]["status"] == "created"
    assert FakeCoverAdapter.calls == ["one"]


def test_fetch_covers_cli_filters_by_collection(tmp_path: Path, capsys, monkeypatch) -> None:
    add_video(tmp_path / "archive", "one", collection="Другое")
    FakeCoverAdapter.calls = []
    monkeypatch.setattr(cli, "YtDlpTikTokAdapter", FakeCoverAdapter)
    monkeypatch.setattr(cli, "GalleryDlTikTokSlidesAdapter", FakeCoverAdapter)

    assert main(arguments(tmp_path, "--collection", "Нейронки")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["selected"] == 0
    assert FakeCoverAdapter.calls == []


def test_fetch_covers_cli_rerun_skips(tmp_path: Path, capsys, monkeypatch) -> None:
    add_video(tmp_path / "archive", "one")
    FakeCoverAdapter.calls = []
    monkeypatch.setattr(cli, "YtDlpTikTokAdapter", FakeCoverAdapter)
    monkeypatch.setattr(cli, "GalleryDlTikTokSlidesAdapter", FakeCoverAdapter)

    assert main(arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(arguments(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert (output["created"], output["skipped"]) == (0, 1)


def test_fetch_covers_cli_reports_failure(tmp_path: Path, capsys, monkeypatch) -> None:
    add_video(tmp_path / "archive", "one")
    FakeCoverAdapter.calls = []
    FakeCoverAdapter.image = None
    monkeypatch.setattr(cli, "YtDlpTikTokAdapter", FakeCoverAdapter)
    monkeypatch.setattr(cli, "GalleryDlTikTokSlidesAdapter", FakeCoverAdapter)

    assert main(arguments(tmp_path)) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["failed"] == 1
    assert output["results"][0]["reason"] == "adapter returned no image artifact"
