from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sourcefolio.cli import main


def test_decision_new_and_search(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    arguments = [
        "decision-new",
        "--vault",
        str(vault),
        "--title",
        "Use SQLite",
        "--problem",
        "Need durable state",
        "--option",
        "SQLite",
        "--option",
        "JSON files",
        "--chosen",
        "SQLite",
        "--rationale",
        "Transactional and local",
        "--follow-up",
        "Add migrations",
        "--project",
        "midas",
        "--tag",
        "storage",
        "--date",
        "2026-11-08",
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["created"] is True
    assert first["path"] == "2026-11-08-use-sqlite.md"
    assert first["decisions_root"] == str((vault / "decisions").resolve())

    note = vault / "decisions" / "2026-11-08-use-sqlite.md"
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "Transactional and local" in content
    assert "midas" in content

    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["created"] is False

    assert main(["decision-search", "sqlite", "--vault", str(vault)]) == 0
    search = json.loads(capsys.readouterr().out)
    assert search["count"] == 1
    assert search["hits"][0]["title"] == "Use SQLite"
    assert search["hits"][0]["date"] == "2026-11-08"
    assert search["hits"][0]["status"] == "open"


def test_decision_new_defaults_to_today(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"

    assert main(["decision-new", "--vault", str(vault), "--title", "Adopt X"]) == 0
    output = json.loads(capsys.readouterr().out)
    today = datetime.now(UTC).date().isoformat()
    assert output["path"] == f"{today}-adopt-x.md"
    assert (vault / "decisions" / output["path"]).is_file()
