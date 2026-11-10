from __future__ import annotations

import json
from pathlib import Path

from sourcefolio.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "manual-capture" / "manifest.json"


def test_note_command_roundtrip(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    vault = tmp_path / "vault"
    assert main(["archive-capture", str(EXAMPLE), "--archive-dir", str(archive)]) == 0
    source_id = json.loads(capsys.readouterr().out)["source_id"]

    assert (
        main(
            [
                "note",
                source_id,
                "--my-title",
                "Про агентов",
                "--status",
                "keep",
                "--archive-dir",
                str(archive),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["created"] is True
    note_path = Path(output["note"])
    assert note_path == vault / "ideas" / f"{source_id}.md"
    content = note_path.read_text(encoding="utf-8")
    assert "my_title: Про агентов" in content
    assert "status: keep" in content
    assert "## Заметки" in content

    assert (
        main(
            [
                "note",
                source_id,
                "--why",
                "для статьи",
                "--archive-dir",
                str(archive),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    second = json.loads(capsys.readouterr().out)
    assert second["created"] is False
    assert "why_saved: для статьи" in note_path.read_text(encoding="utf-8")

    assert (
        main(
            [
                "note",
                source_id,
                "--clear",
                "my-title",
                "--archive-dir",
                str(archive),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    third = json.loads(capsys.readouterr().out)
    assert third["applied"] == {"my_title": None}
    assert "my_title" not in note_path.read_text(encoding="utf-8")


def test_note_command_unknown_source_returns_one(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    vault = tmp_path / "vault"
    assert main(["archive-capture", str(EXAMPLE), "--archive-dir", str(archive)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "note",
                "src_" + "0" * 64,
                "--my-title",
                "x",
                "--archive-dir",
                str(archive),
                "--vault",
                str(vault),
            ]
        )
        == 1
    )
