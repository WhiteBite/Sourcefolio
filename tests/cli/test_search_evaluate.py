from __future__ import annotations

import json
from pathlib import Path

from evidence_vault.cli import main

ROOT = Path(__file__).parents[2]
EXPORT = ROOT / "examples" / "tiktok-export" / "user_data.json"
QUERIES = ROOT / "examples" / "search-evaluation" / "queries.json"


def test_search_evaluation_cli(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    assert (
        main(
            [
                "tiktok-import",
                str(EXPORT),
                "--archive-dir",
                str(archive),
                "--control-db",
                str(tmp_path / "control.sqlite"),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "search-evaluate",
                str(QUERIES),
                "--archive-dir",
                str(archive),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["schema_version"] == "search-evaluation-report/v1"
    assert report["query_count"] == 2
    assert report["hit_rate"] == 1.0
    assert report["mean_reciprocal_rank"] == 1.0
    assert report["mean_recall"] == 1.0
    assert all(query["results"][0]["relevant"] for query in report["queries"])
