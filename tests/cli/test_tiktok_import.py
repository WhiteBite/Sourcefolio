from __future__ import annotations

import json
from pathlib import Path

from sourcefolio.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"


def test_tiktok_inspect_does_not_write_data(tmp_path: Path, capsys) -> None:
    result = main(
        [
            "tiktok-inspect",
            str(EXAMPLE),
            "--export-timezone",
            "Europe/Moscow",
            "--limit",
            "2",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["entries_read"] == 3
    assert output["unique_tasks"] == 3
    assert output["returned_tasks"] == 2
    assert len(output["items"]) == 2
    assert not (tmp_path / "data").exists()


def test_tiktok_import_is_resumable_and_bounded(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"
    arguments = [
        "tiktok-import",
        str(EXAMPLE),
        "--archive-dir",
        str(archive),
        "--control-db",
        str(control),
        "--concurrency",
        "2",
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["results"]["succeeded"] == 3
    assert first["results"]["failed"] == 0
    assert second["results"]["skipped"] == 3
    assert len(list((archive / "sources").rglob("*.json"))) == 3
    assert control.exists()
