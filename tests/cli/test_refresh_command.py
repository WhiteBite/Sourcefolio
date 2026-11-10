from __future__ import annotations

import json
from pathlib import Path

from sourcefolio import cli
from sourcefolio.cli import main

FIXTURE = Path(__file__).parents[1] / "fixtures" / "collection-capture.json"


class FakeChatClient:
    def __init__(self, config: object) -> None:
        pass

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "{}"


def write_projects(path: Path) -> None:
    path.write_text(
        json.dumps(
            {"projects": [{"id": "midas", "name": "MidasAI", "description": "trading bot"}]}
        ),
        encoding="utf-8",
    )


def test_refresh_runs_project_obsidian_only(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    write_projects(projects)

    assert (
        main(
            [
                "refresh",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
                "--projects",
                str(projects),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert [step["step"] for step in output["steps"]] == ["project-obsidian", "render-ui"]
    assert (vault / "_generated" / "sourcefolio" / "source-cards").is_dir()


def test_refresh_chains_import_collections(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    control = tmp_path / "control" / "capture.sqlite3"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    write_projects(projects)

    assert (
        main(
            [
                "refresh",
                "--capture",
                str(FIXTURE),
                "--archive-dir",
                str(archive),
                "--control-db",
                str(control),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
                "--projects",
                str(projects),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert [step["step"] for step in output["steps"]] == [
        "import-collections",
        "project-obsidian",
        "render-ui",
    ]


def test_refresh_fit_dry_run_includes_project_fit(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    write_projects(projects)
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "refresh",
                "--fit",
                "--dry-run",
                "--collection",
                "X",
                "--api-key",
                "test-key",
                "--projects",
                str(projects),
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
    output = json.loads(capsys.readouterr().out)
    assert "project-fit" in [step["step"] for step in output["steps"]]


def test_refresh_step_failure_returns_one(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    write_projects(projects)
    monkeypatch.delenv("EV_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_LLM_CONFIG_PATH", tmp_path / "missing.json")

    result = main(
        [
            "refresh",
            "--fit",
            "--projects",
            str(projects),
            "--archive-dir",
            str(archive),
            "--derived-dir",
            str(derived),
            "--vault",
            str(vault),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 1
    fit_step = next(step for step in output["steps"] if step["step"] == "project-fit")
    assert "error" in fit_step
    assert "exit" not in fit_step
