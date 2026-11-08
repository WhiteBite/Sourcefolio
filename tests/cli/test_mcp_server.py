from __future__ import annotations

from pathlib import Path

import pytest


def test_schema_tool_returns_json() -> None:
    pytest.importorskip("mcp")
    from evidence_vault import mcp_server

    output = mcp_server.schema("source-packet")

    assert '"title"' in output


def test_archive_audit_tool_runs_on_empty_archive(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from evidence_vault import mcp_server

    output = mcp_server.archive_audit(archive_dir=str(tmp_path))

    assert '"ok": true' in output


def test_project_fit_tool_reports_missing_projects(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from evidence_vault import mcp_server

    output = mcp_server.project_fit(collection=["X"], projects=str(tmp_path / "missing.json"))

    assert "error" in output


def test_context_tool_runs_on_empty_vault(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from evidence_vault import mcp_server

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
    from evidence_vault import mcp_server

    output = mcp_server.decision_search(query="x", vault=str(tmp_path / "vault"))

    assert '"count": 0' in output


def test_scaffold_projects_tool_reports_missing_projects(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from evidence_vault import mcp_server

    output = mcp_server.scaffold_projects(
        vault=str(tmp_path / "vault"),
        projects=str(tmp_path / "missing.json"),
    )

    assert "error" in output
