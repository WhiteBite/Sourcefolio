from __future__ import annotations

import json
from pathlib import Path

from sourcefolio.cli import main


def test_manual_capture_cli_end_to_end(tmp_path: Path, capsys) -> None:
    example = Path(__file__).parents[2] / "examples" / "manual-capture"
    archive = tmp_path / "archive"

    arguments = [
        "archive-capture",
        str(example / "manifest.json"),
        "--archive-dir",
        str(archive),
    ]
    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["created"] is True
    assert second["created"] is False
    assert first["capture_id"] == second["capture_id"]
    assert len(first["artifact_ids"]) == 1


def test_schema_export(capsys) -> None:
    assert main(["schema", "source-packet"]) == 0
    schema = json.loads(capsys.readouterr().out)

    assert schema["type"] == "object"
    assert "SourcePacket" in schema["title"]
    assert "capture_id" in schema["properties"]
