from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemHumanNoteStore,
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
    title: str | None,
    *,
    status: CaptureStatus = CaptureStatus.COMPLETE,
    limitations: tuple[str, ...] = (),
    media_type: str = "text/plain",
    role: ArtifactRole = ArtifactRole.CAPTION_TEXT,
    saved: dict[str, object] | None = None,
) -> str:
    result = ArchiveCapture(
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
            metadata={"saved_index": saved} if saved is not None else {},
            artifacts=(
                ArtifactPayload(
                    role=role,
                    media_type=media_type,
                    stream=BytesIO(f"payload {external_id}".encode()),
                ),
            ),
        )
    )
    return result.packet.source_id


def extract_payload(html: str) -> dict:
    start = html.index(DATA_MARKER) + len(DATA_MARKER)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def build_projection(
    archive: Path,
    tmp_path: Path,
    *,
    limit: int | None = None,
    notes: FileSystemHumanNoteStore | None = None,
) -> UiViewProjection:
    return UiViewProjection(
        packets=FileSystemSourcePacketRepository(archive),
        artifacts=FileSystemArtifactStore(archive),
        store=FileSystemProjectionStore(tmp_path / "vault" / "_generated" / "sourcefolio"),
        notes=notes,
        limit=limit,
    )


def read_page(tmp_path: Path) -> str:
    return (tmp_path / "vault" / "_generated" / "sourcefolio" / "ui" / "index.html").read_text(
        encoding="utf-8"
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
    html = read_page(tmp_path)
    assert html
    assert DATA_MARKER in html
    assert "src_" in html and "cap_" in html

    payload = extract_payload(html)
    assert payload["schema_version"] == "sourcefolio-ui/v1"
    assert payload["generator"] == "sourcefolio"
    assert payload["generator_version"] == "4"
    assert payload["counts"] == {"sources": 2, "complete": 1, "partial": 1}
    assert payload["collections"] == [{"name": "Нейронки", "count": 2}]

    item = payload["items"][0]
    assert set(item) == {
        "source_id",
        "capture_id",
        "title",
        "display_title",
        "snippet",
        "marker",
        "hashtags",
        "thumb_uri",
        "platform",
        "kind",
        "external_id",
        "canonical_url",
        "captured_at",
        "status",
        "media_state",
        "collections",
        "author",
        "video_type",
        "post_created_at",
        "description",
        "stats",
        "collector",
        "limitations",
        "artifacts",
        "derived",
        "annotation",
    }
    assert item["platform"] == "tiktok"
    assert item["kind"] == "video"
    assert {entry["title"] for entry in payload["items"]} == {"Первый ролик", "Второй ролик"}
    assert {entry["display_title"] for entry in payload["items"]} == {
        "Первый ролик",
        "Второй ролик",
    }
    assert {entry["media_state"] for entry in payload["items"]} == {"complete", "partial"}
    assert item["collector"] == {
        "actor_kind": "tool",
        "actor_id": "yt-dlp",
        "method": "download",
        "version": "2026.1",
    }
    assert item["artifacts"][0]["uri"].startswith("file://")
    image_item = next(entry for entry in payload["items"] if entry["artifacts"][0]["is_image"])
    assert image_item["artifacts"][0]["is_video"] is False
    assert item["stats"] == {}
    assert item["derived"] == {"count": 0, "kinds": [], "has_text": False, "excerpts": []}
    assert item["annotation"] == {
        "my_title": None,
        "why_saved": None,
        "status": None,
        "note_preview": None,
    }

    partial = next(entry for entry in payload["items"] if entry["status"] == "partial")
    assert partial["limitations"] == ["video stream unavailable"]
    assert partial["media_state"] == "partial"
    complete = next(entry for entry in payload["items"] if entry["status"] == "complete")
    assert complete["media_state"] == "complete"


def test_ui_view_escapes_script_breakout(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    hostile = "</script><script>alert(1)</script>"
    add_packet(archive, "1", hostile)

    build_projection(archive, tmp_path).rebuild()

    html = read_page(tmp_path)
    assert "</script><script>alert(1)" not in html
    payload = extract_payload(html)
    assert payload["items"][0]["title"] == hostile
    assert payload["items"][0]["display_title"] == hostile


def test_ui_view_limit_caps_items(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", "Первый ролик")
    add_packet(archive, "2", "Второй ролик")

    assert build_projection(archive, tmp_path, limit=1).rebuild() == 1
    assert extract_payload(read_page(tmp_path))["counts"]["sources"] == 1


def test_ui_view_display_title_fallback_chain(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    long_line = "word " * 30
    add_packet(archive, "1", None, saved={"desc": "first line\nsecond line", "author": "a"})
    add_packet(archive, "2", None, saved={"author": "b"})
    add_packet(archive, "42", None)
    add_packet(archive, "5", None, saved={"desc": long_line})

    build_projection(archive, tmp_path).rebuild()
    payload = extract_payload(read_page(tmp_path))
    by_id = {entry["external_id"]: entry for entry in payload["items"]}

    assert by_id["1"]["display_title"] == "first line"
    assert by_id["1"]["snippet"] == "second line"
    assert by_id["2"]["display_title"] == "@b"
    assert by_id["42"]["display_title"] == "video 42"
    long_title = by_id["5"]["display_title"]
    assert len(long_title) <= 81
    assert len(long_title) < len(long_line.strip())


def test_ui_view_media_states(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", "Комплект")
    add_packet(
        archive,
        "2",
        "Метаданные",
        status=CaptureStatus.PARTIAL,
        limitations=("media capture was not requested",),
    )
    add_packet(
        archive,
        "3",
        "Частичный",
        status=CaptureStatus.PARTIAL,
        limitations=("download failed",),
    )

    build_projection(archive, tmp_path).rebuild()
    payload = extract_payload(read_page(tmp_path))
    by_title = {entry["title"]: entry for entry in payload["items"]}

    assert by_title["Комплект"]["media_state"] == "complete"
    assert by_title["Метаданные"]["media_state"] == "metadata_only"
    assert by_title["Частичный"]["media_state"] == "partial"
    assert by_title["Метаданные"]["status"] == "partial"


def test_ui_view_merges_annotations(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    ideas = tmp_path / "ideas"
    annotated_id = add_packet(archive, "1", "Заголовок")
    scaffolded_id = add_packet(archive, "2", "Заголовок два")
    add_packet(archive, "3", "Без заметки")
    notes = FileSystemHumanNoteStore(ideas)
    annotated_note = (
        "---\n"
        "status: keep\n"
        f"source_id: {annotated_id}\n"
        "my_title: Моё имя\n"
        "why_saved: для диплома\n"
        "---\n"
        "\n"
        "## Заметки\n"
        "\n"
        "свои мысли\n"
    )
    assert notes.create_if_absent(f"{annotated_id}.md", annotated_note)
    scaffolded_note = (
        f"---\nstatus: inbox\nsource_id: {scaffolded_id}\ntitle: Заголовок два\n---\n\n## Заметки\n"
    )
    assert notes.create_if_absent(f"{scaffolded_id}.md", scaffolded_note)

    build_projection(archive, tmp_path, notes=notes).rebuild()
    payload = extract_payload(read_page(tmp_path))
    by_source = {entry["source_id"]: entry for entry in payload["items"]}

    annotated = by_source[annotated_id]
    assert annotated["annotation"] == {
        "my_title": "Моё имя",
        "why_saved": "для диплома",
        "status": "keep",
        "note_preview": "свои мысли",
    }
    assert annotated["display_title"] == "Моё имя"

    scaffolded = by_source[scaffolded_id]
    assert scaffolded["annotation"]["status"] == "inbox"
    assert scaffolded["annotation"]["my_title"] is None
    assert scaffolded["annotation"]["note_preview"] is None

    unannotated = next(entry for entry in payload["items"] if entry["title"] == "Без заметки")
    assert unannotated["annotation"] == {
        "my_title": None,
        "why_saved": None,
        "status": None,
        "note_preview": None,
    }


def test_ui_view_hashtag_only_title_falls_back(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", None, saved={"desc": "#meme", "author": "someone"})

    build_projection(archive, tmp_path).rebuild()
    item = extract_payload(read_page(tmp_path))["items"][0]

    assert item["display_title"] == "@someone"
    assert item["hashtags"] == ["meme"]
    assert item["snippet"] is None


def test_ui_view_marker_matches_collection_initial(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", "Ролик")

    build_projection(archive, tmp_path).rebuild()
    item = extract_payload(read_page(tmp_path))["items"][0]

    assert item["marker"] == item["collections"][0][:1].upper()
