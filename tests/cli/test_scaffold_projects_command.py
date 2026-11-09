from __future__ import annotations

import json
from pathlib import Path

from sourcefolio.cli import main


def write_projects(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "projects": [
                    {"id": "midas", "name": "MidasAI", "description": "trading bot"},
                    {"id": "mafiin", "name": "Mafiin", "description": "party game"},
                ]
            }
        ),
        encoding="utf-8",
    )


def arguments(tmp_path: Path) -> list[str]:
    return [
        "scaffold-projects",
        "--vault",
        str(tmp_path / "vault"),
        "--projects",
        str(tmp_path / "projects.json"),
    ]


def test_scaffold_projects_creates_notes(tmp_path: Path, capsys) -> None:
    write_projects(tmp_path / "projects.json")

    assert main(arguments(tmp_path)) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["created"] == 2
    assert first["skipped"] == 0
    assert sorted(first["created_paths"]) == ["mafiin.md", "midas.md"]
    assert first["projects_root"] == str((tmp_path / "vault" / "projects").resolve())

    note = tmp_path / "vault" / "projects" / "midas.md"
    assert note.is_file()
    assert "trading bot" in note.read_text(encoding="utf-8")

    assert main(arguments(tmp_path)) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["created"] == 0
    assert second["skipped"] == 2
    assert second["created_paths"] == []
