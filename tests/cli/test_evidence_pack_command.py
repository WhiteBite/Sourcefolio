from __future__ import annotations

import json
from pathlib import Path

from sourcefolio.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"


def test_evidence_pack_cli_emits_json_and_markdown(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
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

    arguments = [
        "evidence-pack",
        "7300000000000000001",
        "--archive-dir",
        str(archive),
        "--derived-dir",
        str(derived),
    ]
    assert main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "evidence-pack/v1"
    assert payload["pack_type"] == "retrieval_only"
    assert payload["result_count"] == 1
    assert payload["sources"][0]["rank"] == 1
    assert payload["sources"][0]["artifacts"]
    assert payload["sources"][0]["artifacts"][0]["uri"].startswith("file:")
    assert "answer" not in payload

    assert main([*arguments, "--format", "markdown"]) == 0
    markdown = capsys.readouterr().out
    assert "# Evidence pack" in markdown
    assert "retrieval only" in markdown
    assert "Search scores are lexical relevance only" in markdown
    assert "7300000000000000001" in markdown

    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(payload), encoding="utf-8")
    verify_arguments = [
        "evidence-pack-verify",
        str(pack_path),
        "--archive-dir",
        str(archive),
        "--derived-dir",
        str(derived),
        "--verify-bytes",
    ]
    assert main(verify_arguments) == 0
    integrity = json.loads(capsys.readouterr().out)
    assert integrity["ok"] is True
    assert integrity["bytes_checked"] == integrity["bytes_valid"]

    payload["sources"][0]["artifacts"][0]["role"] = "original_video"
    pack_path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(verify_arguments) == 1
    integrity = json.loads(capsys.readouterr().out)
    assert integrity["ok"] is False
    assert "artifact_metadata_mismatch" in {issue["code"] for issue in integrity["issues"]}
