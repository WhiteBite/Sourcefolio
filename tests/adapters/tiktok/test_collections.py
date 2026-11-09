from __future__ import annotations

import json
from pathlib import Path

import pytest

from sourcefolio.adapters.tiktok.collections import (
    CollectionCaptureFormatError,
    CollectionCaptureParser,
)
from sourcefolio.domain.source import SourceKind

FIXTURE = Path(__file__).parents[2] / "fixtures" / "collection-capture.json"


def test_parser_preserves_collection_memberships() -> None:
    result = CollectionCaptureParser().parse(FIXTURE)

    assert result.entries_read == 4
    assert len(result.tasks) == 2
    video = next(task for task in result.tasks if task.source.external_id == "7300000000000000001")
    assert {item.name for item in video.collections} == {"Нейронки", "Идеи"}
    assert {item.external_id for item in video.collections} == {"7001", "7002"}
    assert len(video.raw_entries) == 2


def test_parser_does_not_fake_saved_at_and_keeps_post_date_in_metadata() -> None:
    result = CollectionCaptureParser().parse(FIXTURE)

    video = next(task for task in result.tasks if task.source.external_id == "7300000000000000001")
    assert video.saved_at is None
    assert video.metadata["post_created_at"] == "2026-01-01T12:00:00.000Z"
    assert video.metadata["type"] == "video"
    assert video.metadata["import"] == "collection_capture"


def test_parser_records_slideshow_type_and_images() -> None:
    result = CollectionCaptureParser().parse(FIXTURE)

    slideshow = next(
        task for task in result.tasks if task.source.external_id == "7300000000000000002"
    )
    assert slideshow.source.kind == SourceKind.SLIDESHOW
    assert slideshow.saved_at is None
    assert slideshow.metadata["type"] == "slideshow"
    assert slideshow.metadata["images"] == [
        "https://cdn.example/slide-1.jpg",
        "https://cdn.example/slide-2.jpg",
    ]


def test_parser_reports_invalid_urls_as_issues() -> None:
    result = CollectionCaptureParser().parse(FIXTURE)

    assert len(result.issues) == 1
    assert result.issues[0].collection_index == 1
    assert result.issues[0].item_index == 1
    assert result.issues[0].reason


def test_parser_ignores_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "collection-capture/v1",
                "captured_at": "2026-11-08T10:00:00.000Z",
                "platform": "tiktok",
                "account": {"sec_uid": "x", "future_account_field": 1},
                "collections": [
                    {
                        "collection_id": "1",
                        "name": "A",
                        "expected": 1,
                        "actual": 1,
                        "future_collection_field": True,
                        "items": [
                            {
                                "video_id": "9",
                                "type": "video",
                                "url": "https://www.tiktok.com/@a/video/9",
                                "future_item_field": {"x": 1},
                            }
                        ],
                    }
                ],
                "run": {"mode": "full", "future_run_field": "y"},
                "future_top_field": [1, 2],
            }
        ),
        encoding="utf-8",
    )

    result = CollectionCaptureParser().parse(path)

    assert len(result.tasks) == 1
    assert result.tasks[0].source.external_id == "9"
    assert result.tasks[0].collections[0].name == "A"


def test_parser_rejects_wrong_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "collection-capture/v2",
                "captured_at": "2026-11-08T10:00:00.000Z",
                "platform": "tiktok",
                "account": {"sec_uid": "x"},
                "collections": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CollectionCaptureFormatError):
        CollectionCaptureParser().parse(path)


def test_parser_warns_when_collection_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "collection-capture/v1",
                "captured_at": "2026-11-08T10:00:00.000Z",
                "platform": "tiktok",
                "account": {"sec_uid": "x"},
                "collections": [
                    {
                        "collection_id": "1",
                        "name": "A",
                        "expected": 5,
                        "actual": 1,
                        "items": [
                            {
                                "video_id": "9",
                                "type": "video",
                                "url": "https://www.tiktok.com/@a/video/9",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = CollectionCaptureParser().parse(path)

    assert len(result.warnings) == 1
    assert "expected 5" in result.warnings[0]
