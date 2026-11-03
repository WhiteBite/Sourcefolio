from __future__ import annotations

import json
from pathlib import Path

from evidence_vault.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "manual-capture" / "manifest.json"


def test_archive_audit_cli_detects_corruption(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    assert main(["archive-capture", str(EXAMPLE), "--archive-dir", str(archive)]) == 0
    capsys.readouterr()
    arguments = ["archive-audit", "--archive-dir", str(archive), "--verify-bytes"]

    assert main(arguments) == 0
    clean = json.loads(capsys.readouterr().out)
    assert clean["ok"] is True
    assert clean["packets"] == 1
    assert clean["valid_artifacts"] == clean["unique_artifacts"]

    artifact = next((archive / "artifacts").glob("[0-9a-f][0-9a-f]/*"))
    artifact.write_bytes(b"corrupt")
    assert main(arguments) == 1
    corrupted = json.loads(capsys.readouterr().out)
    assert corrupted["ok"] is False
    assert corrupted["issues"][0]["kind"] == "integrity_mismatch"
