from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

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


def test_refresh_rebuilds_registry_and_views_by_default(tmp_path: Path, capsys) -> None:
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
    assert [step["step"] for step in output["steps"]] == [
        "registry-build",
        "project-obsidian",
        "context",
        "render-ui",
    ]
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
        "registry-build",
        "project-obsidian",
        "context",
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
    assert [step["step"] for step in output["steps"]] == [
        "project-fit",
        "registry-build",
        "project-obsidian",
        "context",
        "render-ui",
    ]


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


def test_refresh_registry_without_model_config_records_step_error(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    write_projects(projects)

    def missing_config(**_kwargs: object) -> cli.LlmConfig:
        raise ValueError("no api key")

    monkeypatch.setattr(cli, "resolve_llm_config", missing_config)

    result = main(
        [
            "refresh",
            "--registry",
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
    assert [step["step"] for step in output["steps"]] == [
        "registry-extract",
        "registry-build",
        "project-obsidian",
        "context",
        "render-ui",
    ]
    registry_step = output["steps"][0]
    assert registry_step["error"] == "no api key"
    assert [step["exit"] for step in output["steps"][1:]] == [0, 0, 0, 0]


def test_refresh_registry_runs_extract_before_build(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    write_projects(projects)
    monkeypatch.setattr(
        cli,
        "resolve_llm_config",
        lambda **_kwargs: cli.LlmConfig("https://example.test", "test-key", "test-model"),
    )
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "refresh",
                "--registry",
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

    assert [step["step"] for step in output["steps"]] == [
        "registry-extract",
        "registry-build",
        "project-obsidian",
        "context",
        "render-ui",
    ]


def test_refresh_registry_runs_after_project_fit(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    write_projects(projects)
    monkeypatch.setattr(
        cli,
        "resolve_llm_config",
        lambda **_kwargs: cli.LlmConfig("https://example.test", "test-key", "test-model"),
    )
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "refresh",
                "--fit",
                "--registry",
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

    assert [step["step"] for step in output["steps"]] == [
        "project-fit",
        "registry-extract",
        "registry-build",
        "project-obsidian",
        "context",
        "render-ui",
    ]


def test_refresh_passes_projects_to_render_ui(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "custom-projects.json"
    write_projects(projects)
    recorded: list[argparse.Namespace] = []

    def fake_render_ui(args: argparse.Namespace) -> int:
        recorded.append(args)
        return 0

    monkeypatch.setattr(cli, "_render_ui", fake_render_ui)

    assert (
        main(
            [
                "refresh",
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
    capsys.readouterr()

    assert recorded[-1].projects == str(projects)


def _stub_refresh_handlers(monkeypatch: pytest.MonkeyPatch) -> list[argparse.Namespace]:
    recorded: list[argparse.Namespace] = []

    def fake_registry_extract(args: argparse.Namespace) -> int:
        recorded.append(args)
        return 0

    def noop(args: argparse.Namespace) -> int:
        return 0

    monkeypatch.setattr(cli, "_registry_extract", fake_registry_extract)
    monkeypatch.setattr(cli, "_registry_build", noop)
    monkeypatch.setattr(cli, "_project_obsidian", noop)
    monkeypatch.setattr(cli, "_context", noop)
    monkeypatch.setattr(cli, "_render_ui", noop)
    return recorded


def test_refresh_registry_extract_uses_new_and_default_profiles(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    recorded = _stub_refresh_handlers(monkeypatch)

    assert (
        main(
            [
                "refresh",
                "--registry",
                "--archive-dir",
                str(tmp_path / "archive"),
                "--derived-dir",
                str(tmp_path / "derived"),
                "--vault",
                str(tmp_path / "vault"),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert len(recorded) == 1
    assert recorded[0].new is True
    assert recorded[0].profiles == "data/vault/projects"


def test_refresh_registry_extract_keeps_explicit_profiles(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    recorded = _stub_refresh_handlers(monkeypatch)
    profiles = tmp_path / "profiles"

    assert (
        main(
            [
                "refresh",
                "--registry",
                "--profiles",
                str(profiles),
                "--archive-dir",
                str(tmp_path / "archive"),
                "--derived-dir",
                str(tmp_path / "derived"),
                "--vault",
                str(tmp_path / "vault"),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert len(recorded) == 1
    assert recorded[0].new is True
    assert recorded[0].profiles == str(profiles)
