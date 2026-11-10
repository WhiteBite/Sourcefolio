from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.projections import FileSystemProjectionStore
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.ui_view import UiViewProjection
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CaptureStatus,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
DATA_MARKER = '<script id="sourcefolio-data" type="application/json">'


def add_packet(
    root: Path,
    external_id: str,
    title: str,
    *,
    status: CaptureStatus = CaptureStatus.COMPLETE,
    limitations: tuple[str, ...] = (),
    media_type: str = "text/plain",
    role: ArtifactRole = ArtifactRole.CAPTION_TEXT,
) -> None:
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
            collector=Provenance(
                actor_kind=ActorKind.TOOL,
                actor_id="yt-dlp",
                method="download",
                version="2026.1",
            ),
            status=status,
            title=title,
            collections=(CollectionMembership(name="Нейронки"),),
            limitations=limitations,
            artifacts=(
                ArtifactPayload(
                    role=role,
                    media_type=media_type,
                    stream=BytesIO(f"payload {external_id}".encode()),
                ),
            ),
        )
    )


def extract_payload(html: str) -> dict:
    start = html.index(DATA_MARKER) + len(DATA_MARKER)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def build_projection(
    archive: Path, tmp_path: Path, *, limit: int | None = None
) -> UiViewProjection:
    return UiViewProjection(
        packets=FileSystemSourcePacketRepository(archive),
        artifacts=FileSystemArtifactStore(archive),
        store=FileSystemProjectionStore(tmp_path / "vault" / "_generated" / "sourcefolio"),
        limit=limit,
    )


def test_ui_view_writes_single_self_contained_page(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(
        archive, "1", "Первый ролик", media_type="image/png", role=ArtifactRole.ORIGINAL_IMAGE
    )
    add_packet(
        archive,
        "2",
        "Второй ролик",
        status=CaptureStatus.PARTIAL,
        limitations=("video stream unavailable",),
    )
    projection = build_projection(archive, tmp_path)

    assert projection.rebuild() == 2

    generated = tmp_path / "vault" / "_generated" / "sourcefolio" / "ui"
    assert sorted(path.name for path in generated.iterdir()) == ["index.html", "manifest.json"]
    html = (generated / "index.html").read_text(encoding="utf-8")
    assert html
    assert DATA_MARKER in html
    assert "src_" in html and "cap_" in html

    payload = extract_payload(html)
    assert payload["schema_version"] == "sourcefolio-ui/v1"
    assert payload["generator"] == "sourcefolio"
    assert payload["generator_version"] == "1"
    assert payload["counts"] == {"sources": 2, "complete": 1, "partial": 1}
    assert payload["collections"] == [{"name": "Нейронки", "count": 2}]

    item = payload["items"][0]
    assert set(item) == {
        "source_id",
        "capture_id",
        "title",
        "platform",
        "kind",
        "external_id",
        "canonical_url",
        "captured_at",
        "status",
        "collections",
        "author",
        "video_type",
        "post_created_at",
        "description",
        "collector",
        "limitations",
        "artifacts",
        "derived",
    }
    assert item["platform"] == "tiktok"
    assert item["kind"] == "video"
    assert item["collector"] == {
        "actor_kind": "tool",
        "actor_id": "yt-dlp",
        "method": "download",
        "version": "2026.1",
    }
    assert item["artifacts"][0]["uri"].startswith("file://")
    image_item = next(entry for entry in payload["items"] if entry["artifacts"][0]["is_image"])
    assert image_item["artifacts"][0]["is_video"] is False
    assert item["derived"] == {"count": 0, "kinds": [], "has_text": False, "excerpts": []}

    partial = next(entry for entry in payload["items"] if entry["status"] == "partial")
    assert partial["limitations"] == ["video stream unavailable"]


def test_ui_view_escapes_script_breakout(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    hostile = "</script><script>alert(1)</script>"
    add_packet(archive, "1", hostile)

    build_projection(archive, tmp_path).rebuild()

    html = (tmp_path / "vault" / "_generated" / "sourcefolio" / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "</script><script>alert(1)" not in html
    assert extract_payload(html)["items"][0]["title"] == hostile


def test_ui_view_limit_caps_items(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", "Первый ролик")
    add_packet(archive, "2", "Второй ролик")

    assert build_projection(archive, tmp_path, limit=1).rebuild() == 1

    html = (tmp_path / "vault" / "_generated" / "sourcefolio" / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    assert extract_payload(html)["counts"]["sources"] == 1
