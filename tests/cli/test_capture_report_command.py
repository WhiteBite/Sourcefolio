from __future__ import annotations

import json
from pathlib import Path

from sourcefolio.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"


def test_capture_report_cli_json_and_markdown(tmp_path: Path, capsys) -> None:
    control = tmp_path / "control.sqlite"
    assert (
        main(
            [
                "tiktok-import",
                str(EXAMPLE),
                "--archive-dir",
                str(tmp_path / "archive"),
                "--control-db",
                str(control),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["capture-report", "--control-db", str(control)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total_jobs"] == 3
    assert report["status_counts"]["succeeded"] == 3
    assert report["kind_status_counts"]["video"]["succeeded"] == 1
    assert report["kind_status_counts"]["slideshow"]["succeeded"] == 1
    assert report["attempt_duration_seconds"]["count"] == 3

    assert main(["capture-report", "--control-db", str(control), "--format", "markdown"]) == 0
    markdown = capsys.readouterr().out
    assert "# Capture report" in markdown
    assert "Total jobs: **3**" in markdown
