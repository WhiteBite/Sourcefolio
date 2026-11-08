from __future__ import annotations

import json
from pathlib import Path

from evidence_vault.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"


def test_obsidian_projection_and_search_cli(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    vault = tmp_path / "vault"
    assert (
        main(
            [
                "tiktok-import",
                str(EXAMPLE),
                "--archive-dir",
                str(archive),
                "--control-db",
                str(tmp_path / "control.sqlite"),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "project-obsidian",
                "--archive-dir",
                str(archive),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    projection = json.loads(capsys.readouterr().out)
    assert projection["documents"] == 3
    assert projection["repo_node_documents"] == 0
    assert projection["graph_documents"] == 1
    assert projection["decision_documents"] == 1
    cards = list((vault / "_generated" / "evidence-vault" / "source-cards").rglob("*.md"))
    assert len(cards) == 3
    generated = vault / "_generated" / "evidence-vault"
    assert (generated / "entities").is_dir()
    assert (generated / "graph" / "index.md").is_file()
    assert (generated / "decisions" / "index.md").is_file()

    assert main(["search", "Нейронки", "--archive-dir", str(archive)]) == 0
    search = json.loads(capsys.readouterr().out)
    assert search["count"] == 1
    assert search["results"][0]["collections"] == ["Нейронки"]
    assert search["results"][0]["relevance_score"] > 0
    assert search["results"][0]["snippet"]
