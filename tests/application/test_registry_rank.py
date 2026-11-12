from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.registry_rank import rank_captures, score_capture
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeRecord:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeExtractions:
    def __init__(self, texts_by_capture: dict[str, list[str]]) -> None:
        self._texts_by_capture = texts_by_capture

    def iter_for_capture(self, capture_id: str) -> object:
        return iter(FakeRecord(text) for text in self._texts_by_capture.get(capture_id, []))


def add_packet(
    root: Path,
    external_id: str,
    *,
    collection: str = "Нейронки",
    desc: str = "текст",
    comments: list[dict[str, object]] | None = None,
    plays: int | None = None,
    likes: int | None = None,
    captured_at: datetime = NOW,
) -> SourcePacket:
    saved: dict[str, object] = {
        "author": "author",
        "desc": desc,
        "comments": comments
        if comments is not None
        else [{"text": "класс", "author": "bob", "likes": 2}],
    }
    if plays is not None or likes is not None:
        stats: dict[str, int] = {}
        if plays is not None:
            stats["plays"] = plays
        if likes is not None:
            stats["likes"] = likes
        saved["stats"] = stats
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
                collections=(CollectionMembership(name=collection),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption"),
                    ),
                ),
                metadata={"saved_index": saved},
            )
        )
        .packet
    )


def test_repo_heavy_packet_scores_nine(tmp_path: Path) -> None:
    packet = add_packet(
        tmp_path,
        "one",
        desc="Полезное описание https://github.com/acme/tool и длинный текст не менее 40 символов",
        comments=[
            {"text": "первый", "author": "a", "likes": 1},
            {"text": "второй", "author": "b", "likes": 1},
            {"text": "третий", "author": "c", "likes": 1},
        ],
        plays=100,
        likes=5,
    )

    score = score_capture(packet, "def main():\n    pass")

    assert score.score == 9
    assert score.has_repository is True
    assert score.has_code is True
    assert score.has_text is True
    assert score.comment_count == 3
    assert score.engagement_ratio == 0.05
    assert score.description_key != ""


def test_no_text_packet_is_negative(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="", comments=[])

    score = score_capture(packet, "")

    assert score.score == -3
    assert score.has_text is False
    assert score.has_repository is False
    assert score.has_code is False
    assert score.description_key == ""


def test_duplicate_descriptions_penalized_once(tmp_path: Path) -> None:
    first = add_packet(tmp_path, "one", desc="Одинаковое описание")
    second = add_packet(tmp_path, "two", desc="  одинаковое   ОПИСАНИЕ  ")

    ranked = rank_captures([first, second], None)

    assert len(ranked) == 2
    assert ranked[0].description_key == ranked[1].description_key
    assert ranked[0].score == ranked[1].score + 1


def test_high_engagement_adds_one(tmp_path: Path) -> None:
    high = add_packet(tmp_path, "one", desc="описание", plays=100, likes=5)
    low = add_packet(tmp_path, "two", desc="описание два", plays=100, likes=1)

    high_score = score_capture(high, "")
    low_score = score_capture(low, "")

    assert high_score.score == low_score.score + 1
    assert high_score.engagement_ratio == 0.05
    assert low_score.engagement_ratio == 0.01


def test_comment_rich_adds_one(tmp_path: Path) -> None:
    rich = add_packet(
        tmp_path,
        "one",
        desc="описание",
        comments=[
            {"text": "раз", "author": "a", "likes": 0},
            {"text": "два", "author": "b", "likes": 0},
            {"text": "три", "author": "c", "likes": 0},
        ],
    )
    sparse = add_packet(
        tmp_path, "two", desc="описание два", comments=[{"text": "раз", "author": "a"}]
    )

    rich_score = score_capture(rich, "")
    sparse_score = score_capture(sparse, "")

    assert rich_score.score == sparse_score.score + 1
    assert rich_score.comment_count == 3
    assert sparse_score.comment_count == 1


def test_rank_is_stable_across_runs(tmp_path: Path) -> None:
    packets = [
        add_packet(tmp_path, "one", desc="первое описание"),
        add_packet(tmp_path, "two", desc="второе описание"),
        add_packet(tmp_path, "three", desc="третье описание"),
    ]

    first = rank_captures(packets, None)
    second = rank_captures(packets, None)

    assert first == second
    assert [item.capture_id for item in first] == [item.capture_id for item in second]


def test_ocr_truncation_keeps_non_ascii_safe(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="описание")
    filler = "Ж" * 2000
    extractions = FakeExtractions({packet.capture_id: [filler + " def hidden()"]})

    ranked = rank_captures([packet], extractions, max_ocr_kb=1)

    assert len(ranked) == 1
    assert ranked[0].has_code is False
    assert ranked[0].has_text is True


def test_ocr_code_marker_before_limit_is_detected(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="описание")
    extractions = FakeExtractions({packet.capture_id: ["import os\n" + "Ж" * 2000]})

    ranked = rank_captures([packet], extractions, max_ocr_kb=1)

    assert ranked[0].has_code is True


def test_latest_capture_per_source_wins(tmp_path: Path) -> None:
    older = add_packet(tmp_path, "one", desc="старый текст", captured_at=NOW)
    newer = add_packet(
        tmp_path,
        "one",
        desc="новый текст",
        captured_at=NOW.replace(hour=13),
    )

    ranked = rank_captures([older, newer], None)

    assert len(ranked) == 1
    assert ranked[0].capture_id == newer.capture_id
