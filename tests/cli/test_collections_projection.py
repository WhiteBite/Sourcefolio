from __future__ import annotations

import json
from pathlib import Path

from evidence_vault.cli import main

FIXTURE = Path(__file__).parents[1] / "fixtures" / "collection-capture.json"


def _import_arguments(archive: Path, control: Path) -> list[str]:
    return [
        "import-collections",
        str(FIXTURE),
        "--archive-dir",
        str(archive),
        "--control-db",
        str(control),
    ]


def test_import_then_project_builds_knowledge_base(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"
    vault = tmp_path / "vault"

    assert main(_import_arguments(archive, control)) == 0
    capsys.readouterr()

    assert main(["project-obsidian", "--archive-dir", str(archive), "--vault", str(vault)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["documents"] == 2
    assert output["collection_documents"] == 3
    assert output["dashboard_documents"] == 4
    assert output["ideas_created"] == 0

    generated = vault / "_generated" / "evidence-vault"
    assert (generated / "collections" / "neyronki.md").exists()
    assert (generated / "collections" / "idei.md").exists()
    assert (generated / "collections" / "index.md").exists()
    assert (generated / "dashboards" / "inbox.md").exists()
    assert list((vault / "ideas").glob("*.md")) == []

    scaffold_args = [
        "project-obsidian",
        "--archive-dir",
        str(archive),
        "--vault",
        str(vault),
        "--scaffold-ideas",
    ]
    assert main(scaffold_args) == 0
    scaffolded = json.loads(capsys.readouterr().out)
    assert scaffolded["ideas_created"] == 2
    assert len(list((vault / "ideas").glob("*.md"))) == 2

    assert main(scaffold_args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["ideas_created"] == 0
    assert second["ideas_skipped"] == 2


def test_project_extracts_repository_links(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"
    vault = tmp_path / "vault"
    source = tmp_path / "collections.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "collection-capture/v1",
                "captured_at": "2026-11-08T10:00:00.000Z",
                "platform": "tiktok",
                "account": {"unique_id": "example"},
                "collections": [
                    {
                        "collection_id": "1",
                        "name": "Нейронки",
                        "expected": 1,
                        "actual": 1,
                        "items": [
                            {
                                "video_id": "1",
                                "type": "video",
                                "url": "https://www.tiktok.com/@a/video/1",
                                "author": "a",
                                "desc": "репозиторий https://github.com/owner/repo #нейронки",
                                "comments": [],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "import-collections",
                str(source),
                "--archive-dir",
                str(archive),
                "--control-db",
                str(control),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["project-obsidian", "--archive-dir", str(archive), "--vault", str(vault)]) == 0
    capsys.readouterr()

    moc = (vault / "_generated" / "evidence-vault" / "collections" / "neyronki.md").read_text(
        encoding="utf-8"
    )
    assert "https://github.com/owner/repo" in moc
    assert "#нейронки (1)" in moc


def test_scaffold_ideas_command(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"
    vault = tmp_path / "vault"

    assert main(_import_arguments(archive, control)) == 0
    capsys.readouterr()

    assert main(["scaffold-ideas", "--archive-dir", str(archive), "--vault", str(vault)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["created"] == 2
    assert len(list((vault / "ideas").glob("*.md"))) == 2
