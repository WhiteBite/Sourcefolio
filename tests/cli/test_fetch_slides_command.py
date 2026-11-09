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


class FakeSlidesImageFetcher:
    payloads: dict[str, bytes] = {}
    calls: list[str] = []

    def __init__(self, config: object = None) -> None:
        self.config = config

    def fetch(self, url: str) -> BytesIO:
        FakeSlidesImageFetcher.calls.append(url)
        return BytesIO(FakeSlidesImageFetcher.payloads[url])


def add_slideshow(root: Path, external_id: str, images: list[str]) -> None:
    ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    ).handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="tiktok",
                kind=SourceKind.SLIDESHOW,
                external_id=external_id,
                canonical_url=f"https://www.tiktok.com/@x/photo/{external_id}",
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
            metadata={"saved_index": {"type": "slideshow", "images": images, "desc": "текст"}},
        )
    )


def arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "fetch-slides",
        "--collection",
        "Нейронки",
        "--archive-dir",
        str(tmp_path / "archive"),
        *extra,
    ]


def test_fetch_slides_cli_archives_images(tmp_path: Path, capsys, monkeypatch) -> None:
    urls = ["https://cdn.example/1.jpeg", "https://cdn.example/2.jpeg"]
    add_slideshow(tmp_path / "archive", "one", urls)
    FakeSlidesImageFetcher.payloads = {url: b"data" for url in urls}
    FakeSlidesImageFetcher.calls = []
    monkeypatch.setattr(cli, "SlidesImageFetcher", FakeSlidesImageFetcher)

    assert main(arguments(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["collection"] == "Нейронки"
    assert output["sources"] == 1
    assert output["images_downloaded"] == 2
    assert output["failed"] == 0
    assert (output["created"], output["skipped"]) == (1, 0)
    assert output["results"][0]["status"] == "created"
    assert FakeSlidesImageFetcher.calls == urls


def test_fetch_slides_cli_reports_failed_image(tmp_path: Path, capsys, monkeypatch) -> None:
    urls = ["https://cdn.example/1.jpeg", "https://cdn.example/2.jpeg"]
    add_slideshow(tmp_path / "archive", "one", urls)
    FakeSlidesImageFetcher.payloads = {urls[0]: b"data"}
    FakeSlidesImageFetcher.calls = []
    monkeypatch.setattr(cli, "SlidesImageFetcher", FakeSlidesImageFetcher)

    assert main(arguments(tmp_path)) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["failed"] == 1
    assert output["images_downloaded"] == 1
    assert output["results"][0]["failed_indexes"] == [2]


def test_fetch_slides_cli_rerun_skips(tmp_path: Path, capsys, monkeypatch) -> None:
    urls = ["https://cdn.example/1.jpeg"]
    add_slideshow(tmp_path / "archive", "one", urls)
    FakeSlidesImageFetcher.payloads = {urls[0]: b"data"}
    FakeSlidesImageFetcher.calls = []
    monkeypatch.setattr(cli, "SlidesImageFetcher", FakeSlidesImageFetcher)

    assert main(arguments(tmp_path)) == 0
    capsys.readouterr()
    assert main(arguments(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert (output["created"], output["skipped"]) == (0, 1)
