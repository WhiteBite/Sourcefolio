from __future__ import annotations

from pathlib import Path

import pytest


def test_schema_tool_returns_json() -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    output = mcp_server.schema("source-packet")

    assert '"title"' in output


def test_archive_audit_tool_runs_on_empty_archive(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    output = mcp_server.archive_audit(archive_dir=str(tmp_path))

    assert '"ok": true' in output


def test_project_fit_tool_reports_missing_projects(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    output = mcp_server.project_fit(collection=["X"], projects=str(tmp_path / "missing.json"))

    assert "error" in output


def test_context_tool_runs_on_empty_vault(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    projects = tmp_path / "projects.json"
    projects.write_text(
        '{"projects": [{"id": "p1", "name": "P1", "description": "d"}]}',
        encoding="utf-8",
    )

    output = mcp_server.context(
        vault=str(tmp_path / "vault"),
        derived_dir=str(tmp_path / "derived"),
        archive_dir=str(tmp_path / "archive"),
        projects=str(projects),
    )

    assert output
    assert "error" not in output


def test_decision_search_tool_on_empty_vault(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    output = mcp_server.decision_search(query="x", vault=str(tmp_path / "vault"))

    assert '"count": 0' in output


def test_scaffold_projects_tool_reports_missing_projects(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    output = mcp_server.scaffold_projects(
        vault=str(tmp_path / "vault"),
        projects=str(tmp_path / "missing.json"),
    )

    assert "error" in output


def test_decision_new_requires_title(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    output = mcp_server.decision_new(title="", vault=str(tmp_path / "vault"))

    assert "error" in output


def test_tool_annotations_expose_read_only_hints() -> None:
    pytest.importorskip("mcp")
    import asyncio

    from sourcefolio import mcp_server

    tools = {tool.name: tool for tool in asyncio.run(mcp_server.server.list_tools())}

    search = tools["search"].annotations
    decision_new = tools["decision_new"].annotations
    assert search is not None
    assert decision_new is not None
    assert search.read_only_hint is True
    assert decision_new.read_only_hint is False
    assert "title" in tools["decision_new"].input_schema["required"]


def test_ask_tool_annotations() -> None:
    pytest.importorskip("mcp")
    import asyncio

    from sourcefolio import mcp_server

    tools = {tool.name: tool for tool in asyncio.run(mcp_server.server.list_tools())}

    annotations = tools["ask"].annotations
    assert annotations is not None
    assert annotations.read_only_hint is False
    assert annotations.open_world_hint is True


def test_profile_draft_tool_annotations() -> None:
    pytest.importorskip("mcp")
    import asyncio

    from sourcefolio import mcp_server

    tools = {tool.name: tool for tool in asyncio.run(mcp_server.server.list_tools())}

    annotations = tools["profile_draft"].annotations
    assert annotations is not None
    assert annotations.read_only_hint is False


def test_profile_draft_tool_runs_on_repo(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Alpha\n\nA demo project.\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "alpha"\ndependencies = ["pydantic"]\n',
        encoding="utf-8",
    )

    output = mcp_server.profile_draft(vault=str(tmp_path / "vault"), repo=str(repo), id="alpha")

    assert '"created": 1' in output
    assert '"error"' not in output


def test_ask_tool_no_llm_on_empty_archive(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    output = mcp_server.ask(
        query="x",
        no_llm=True,
        archive_dir=str(tmp_path / "archive"),
        derived_dir=str(tmp_path / "derived"),
        vault=str(tmp_path / "vault"),
    )

    assert output
    assert '"status"' in output
    assert not output.startswith("error")


def _stub_cli(monkeypatch: pytest.MonkeyPatch, code: int = 0) -> list[list[str]]:
    pytest.importorskip("mcp")
    from sourcefolio import mcp_server

    calls: list[list[str]] = []

    def fake_main(arguments: list[str]) -> int:
        calls.append(list(arguments))
        return code

    monkeypatch.setattr(mcp_server.cli, "main", fake_main)
    return calls


def test_registry_extract_tool_forwards_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cli(monkeypatch)
    from sourcefolio import mcp_server

    mcp_server.registry_extract(
        projects="p.json",
        profiles="profiles",
        archive_dir="arch",
        derived_dir="der",
        vault="v",
        project=["alpha", "beta"],
        collection=["One", "Two"],
        limit=5,
        max_ocr_kb=11,
        max_caption_kb=7,
        refresh=True,
        dry_run=True,
        base_url="http://llm",
        api_key="k",
        model="m",
    )

    assert calls == [
        [
            "registry-extract",
            "--projects",
            "p.json",
            "--profiles",
            "profiles",
            "--archive-dir",
            "arch",
            "--derived-dir",
            "der",
            "--vault",
            "v",
            "--max-ocr-kb",
            "11",
            "--max-caption-kb",
            "7",
            "--project",
            "alpha",
            "--project",
            "beta",
            "--collection",
            "One",
            "--collection",
            "Two",
            "--limit",
            "5",
            "--refresh",
            "--dry-run",
            "--base-url",
            "http://llm",
            "--api-key",
            "k",
            "--model",
            "m",
        ]
    ]


def test_registry_extract_tool_omits_optional_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cli(monkeypatch)
    from sourcefolio import mcp_server

    mcp_server.registry_extract()

    assert calls == [
        [
            "registry-extract",
            "--projects",
            "data/projects.json",
            "--profiles",
            "data/vault/projects",
            "--archive-dir",
            "data/archive",
            "--derived-dir",
            "data/derived",
            "--vault",
            "data/vault",
            "--max-ocr-kb",
            "20",
            "--max-caption-kb",
            "20",
        ]
    ]


def test_registry_build_tool_forwards_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cli(monkeypatch)
    from sourcefolio import mcp_server

    mcp_server.registry_build(
        projects="p.json",
        profiles="profiles",
        derived_dir="der",
        vault="v",
        dry_run=True,
    )

    assert calls == [
        [
            "registry-build",
            "--projects",
            "p.json",
            "--profiles",
            "profiles",
            "--derived-dir",
            "der",
            "--vault",
            "v",
            "--dry-run",
        ]
    ]


def test_registry_build_tool_omits_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cli(monkeypatch)
    from sourcefolio import mcp_server

    mcp_server.registry_build()

    assert calls == [
        [
            "registry-build",
            "--projects",
            "data/projects.json",
            "--profiles",
            "data/vault/projects",
            "--derived-dir",
            "data/derived",
            "--vault",
            "data/vault",
        ]
    ]


def test_candidate_decide_tool_forwards_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cli(monkeypatch)
    from sourcefolio import mcp_server

    mcp_server.candidate_decide(
        candidate_id="cand_1",
        status="accepted",
        rationale="why",
        approved_by="me",
        merge_into="cand_2",
        projects="p.json",
        derived_dir="der",
        vault="v",
    )

    assert calls == [
        [
            "candidate-decide",
            "--candidate-id",
            "cand_1",
            "--status",
            "accepted",
            "--projects",
            "p.json",
            "--derived-dir",
            "der",
            "--vault",
            "v",
            "--rationale",
            "why",
            "--approved-by",
            "me",
            "--merge-into",
            "cand_2",
        ]
    ]


def test_candidate_decide_tool_omits_optional_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cli(monkeypatch)
    from sourcefolio import mcp_server

    mcp_server.candidate_decide(candidate_id="cand_1")

    assert calls == [
        [
            "candidate-decide",
            "--candidate-id",
            "cand_1",
            "--status",
            "inbox",
            "--projects",
            "data/projects.json",
            "--derived-dir",
            "data/derived",
            "--vault",
            "data/vault",
        ]
    ]


def test_registry_tools_surface_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cli(monkeypatch, code=3)
    from sourcefolio import mcp_server

    assert mcp_server.registry_extract().startswith("error (exit 3)")
    assert mcp_server.registry_build().startswith("error (exit 3)")
    assert mcp_server.candidate_decide(candidate_id="cand_1").startswith("error (exit 3)")


def test_registry_tool_annotations() -> None:
    pytest.importorskip("mcp")
    import asyncio

    from sourcefolio import mcp_server

    tools = {tool.name: tool for tool in asyncio.run(mcp_server.server.list_tools())}

    for name in ("registry_extract", "registry_build", "candidate_decide"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False
    assert "candidate_id" in tools["candidate_decide"].input_schema["required"]
