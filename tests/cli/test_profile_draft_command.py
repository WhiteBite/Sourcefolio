from __future__ import annotations

import json
from pathlib import Path

import pytest

from evidence_vault.cli import main


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Alpha\n\nA tool that drafts project profiles from repositories.\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "alpha"',
                'description = "alpha project"',
                'dependencies = ["pydantic>=2", "pyyaml"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def test_profile_draft_from_repo_creates_note(tmp_path: Path, capsys) -> None:
    repo = make_repo(tmp_path / "repo")
    vault = tmp_path / "vault"

    assert (
        main(
            [
                "profile-draft",
                "--repo",
                str(repo),
                "--id",
                "alpha",
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"created", "skipped", "created_paths", "projects_root", "errors"}
    assert output["created"] == 1
    assert output["errors"] == []

    note = vault / "projects" / "alpha.md"
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "id: alpha" in content
    assert "## Purpose" in content
    assert "## Stack" in content


def test_profile_draft_never_overwrites_existing_note(tmp_path: Path, capsys) -> None:
    repo = make_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    arguments = [
        "profile-draft",
        "--repo",
        str(repo),
        "--id",
        "alpha",
        "--vault",
        str(vault),
    ]

    assert main(arguments) == 0
    capsys.readouterr()
    note = vault / "projects" / "alpha.md"
    first = note.read_bytes()

    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["created"] == 0
    assert second["skipped"] == 1
    assert note.read_bytes() == first


def test_profile_draft_reports_unreadable_repo(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"

    assert (
        main(
            [
                "profile-draft",
                "--repo",
                str(tmp_path / "missing"),
                "--id",
                "beta",
                "--vault",
                str(vault),
            ]
        )
        == 1
    )
    output = json.loads(capsys.readouterr().out)
    assert output["errors"]
    assert not (vault / "projects" / "beta.md").exists()


def test_profile_draft_skips_projects_without_repo_path(tmp_path: Path, capsys) -> None:
    repo = make_repo(tmp_path / "repo")
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {"id": "alpha", "name": "Alpha", "repo_path": str(repo)},
                    {"id": "beta", "name": "Beta"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "profile-draft",
                "--projects",
                str(projects),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["created"] == 1
    assert output["created_paths"] == ["alpha.md"]
    assert (vault / "projects" / "alpha.md").is_file()
    assert not (vault / "projects" / "beta.md").exists()


def test_profile_draft_repo_without_id_exits_two(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "profile-draft",
                "--repo",
                str(repo),
                "--vault",
                str(tmp_path / "vault"),
            ]
        )
    assert excinfo.value.code == 2
