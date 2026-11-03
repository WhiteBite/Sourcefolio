from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_vault.adapters.tiktok.export import ExportFormatError, TikTokExportParser
from evidence_vault.domain.source import SourceKind


def write_json(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {"Activity": {"Favorite Videos": {"FavoriteVideoList": entries}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_json_export_is_grouped_without_losing_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "user_data.json"
    entries = [
        {
            "Date": "2026-01-01 12:00:00",
            "Link": "https://www.tiktok.com/@one/video/123?utm=1",
            "Collection": "Нейронки",
            "UnknownNested": {"keep": [1, 2, 3]},
        },
        {
            "Date": "2026-01-02 12:00:00",
            "Link": "https://m.tiktok.com/@two/video/123",
            "Collections": ["Идеи", "Нейронки"],
        },
        {
            "Date": "2026-01-03T15:00:00+03:00",
            "Link": "https://www.tiktok.com/@one/photo/456",
        },
        {"Date": "2026-01-04 12:00:00", "Link": "not-a-url"},
    ]
    write_json(path, entries)

    result = TikTokExportParser().parse(path)

    assert result.entries_read == 4
    assert len(result.tasks) == 2
    assert len(result.issues) == 1
    video = next(task for task in result.tasks if task.source.external_id == "123")
    assert {item.name for item in video.collections} == {"Нейронки", "Идеи"}
    assert len(video.raw_entries) == 2
    assert video.raw_entries[0]["UnknownNested"] == {"keep": [1, 2, 3]} or video.raw_entries[1][
        "UnknownNested"
    ] == {"keep": [1, 2, 3]}
    slideshow = next(task for task in result.tasks if task.source.external_id == "456")
    assert slideshow.source.kind == SourceKind.SLIDESHOW
    assert slideshow.saved_at == datetime(2026, 1, 3, 12, 0, tzinfo=UTC)
    assert "2 naive dates" in result.warnings[0]


def test_grouped_task_is_stable_when_export_order_changes(tmp_path: Path) -> None:
    entries = [
        {
            "Date": "2026-01-01T12:00:00Z",
            "Link": "https://m.tiktok.com/@z/video/123",
            "Collection": "B",
        },
        {
            "Date": "2026-01-02T12:00:00Z",
            "Link": "https://www.tiktok.com/@a/video/123",
            "Collection": "A",
        },
    ]
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    write_json(first_path, entries)
    write_json(second_path, list(reversed(entries)))

    first = TikTokExportParser().parse(first_path).tasks[0]
    second = TikTokExportParser().parse(second_path).tasks[0]

    assert first == second
    assert first.operation_hash("metadata-only/v1") == second.operation_hash("metadata-only/v1")


def test_naive_dates_can_be_normalized_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "user_data.json"
    write_json(
        path,
        [
            {
                "Date": "2026-01-01 12:00:00",
                "Link": "https://www.tiktok.com/@one/video/123",
            }
        ],
    )

    task = TikTokExportParser(naive_date_timezone="Europe/Moscow").parse(path).tasks[0]

    assert task.saved_at == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def test_empty_favorite_list_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "user_data.json"
    write_json(path, [])

    result = TikTokExportParser().parse(path)

    assert result.entries_read == 0
    assert result.tasks == ()


def test_unsupported_json_shape_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "user_data.json"
    path.write_text(json.dumps({"Activity": {"Like List": []}}), encoding="utf-8")

    with pytest.raises(ExportFormatError, match="no supported Favorite Videos list"):
        TikTokExportParser().parse(path)


def test_txt_export_is_streamed_into_tasks(tmp_path: Path) -> None:
    path = tmp_path / "Favorite Videos.txt"
    path.write_text(
        "Date: 2026-01-01 12:00:00\n"
        "Link: https://www.tiktokv.com/share/video/123/\n\n"
        "Date: 2026-01-02 12:00:00\n"
        "Link: https://www.tiktokv.com/share/video/456/\n",
        encoding="utf-8",
    )

    result = TikTokExportParser(naive_date_timezone="UTC").parse(path)

    assert result.entries_read == 2
    assert [task.source.external_id for task in result.tasks] == ["123", "456"]
    assert all(task.source.kind == SourceKind.VIDEO for task in result.tasks)
