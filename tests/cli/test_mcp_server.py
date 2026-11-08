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
