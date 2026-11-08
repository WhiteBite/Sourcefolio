from __future__ import annotations

from pathlib import Path

from evidence_vault.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"


def test_project_obsidian_leaves_sibling_namespaces_and_human_notes_untouched(
    tmp_path: Path, capsys
) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
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

    generated = vault / "_generated" / "evidence-vault"
    seeded = {
        generated / "repos" / "sentinel.md": "# repos sentinel\n",
        generated / "llm" / "sentinel.md": "# llm sentinel\n",
        vault / "decisions" / "2026-01-01-keep.md": "# human decision\n",
        vault / "ideas" / "src_keep.md": "# human idea\n",
    }
    for path, content in seeded.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    before = {path: path.read_bytes() for path in seeded}

    assert (
        main(
            [
                "project-obsidian",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (generated / "entities").is_dir()
    assert (generated / "graph" / "index.md").is_file()
    assert (generated / "decisions" / "index.md").is_file()

    for path, content in before.items():
        assert path.is_file(), path
        assert path.read_bytes() == content, path
