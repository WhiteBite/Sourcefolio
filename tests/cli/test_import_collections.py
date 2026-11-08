from __future__ import annotations

import json
from pathlib import Path

from evidence_vault.cli import main

FIXTURE = Path(__file__).parents[1] / "fixtures" / "collection-capture.json"


def _import_arguments(archive: Path, control: Path, *extra: str) -> list[str]:
    return [
        "import-collections",
        str(FIXTURE),
        "--archive-dir",
        str(archive),
        "--control-db",
        str(control),
        "--concurrency",
        "2",
        *extra,
    ]


def test_import_collections_is_resumable_and_bounded(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"
    arguments = _import_arguments(archive, control)

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["entries_read"] == 4
    assert first["unique_tasks"] == 2
    assert first["submitted_tasks"] == 2
    assert first["results"]["succeeded"] == 2
    assert first["results"]["failed"] == 0
    assert len(first["issues"]) == 1
    assert second["results"]["skipped"] == 2
    assert len(list((archive / "sources").rglob("*.json"))) == 2
    assert control.exists()


def test_import_collections_preserves_memberships_in_packet(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"

    assert main(_import_arguments(archive, control)) == 0
    capsys.readouterr()

    packets = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (archive / "sources").rglob("*.json")
    ]
    video = next(
        packet for packet in packets if packet["source"]["external_id"] == "7300000000000000001"
    )
    assert {item["name"] for item in video["collections"]} == {"Нейронки", "Идеи"}
    assert video["status"] == "partial"
    assert video["limitations"]


def test_import_collections_limit(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"

    result = main(_import_arguments(archive, control, "--limit", "1"))
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["submitted_tasks"] == 1
    assert output["results"]["succeeded"] == 1


def test_search_finds_imported_collection(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"

    assert main(_import_arguments(archive, control)) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "search",
                "первое",
                "--archive-dir",
                str(archive),
                "--collection",
                "Нейронки",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)

    assert output["count"] == 1
    assert output["results"][0]["external_id"] == "7300000000000000001"
