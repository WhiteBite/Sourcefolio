from __future__ import annotations

import json
from pathlib import Path

import sourcefolio.cli as cli
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


def test_tiktok_download_no_captions_disables_caption_download(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Adapter:
        operation_key = "test-operation"

        def __init__(self, config) -> None:
            captured["config"] = config

    monkeypatch.setattr(cli, "YtDlpTikTokAdapter", Adapter)
    monkeypatch.setattr(cli, "_run_tiktok_batch", lambda args, adapter, partial_is_failure: 0)

    assert (
        main(
            [
                "tiktok-download",
                "export.json",
                "--no-captions",
            ]
        )
        == 0
    )

    config = captured["config"]
    assert config.captions is False
