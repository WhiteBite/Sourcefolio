from __future__ import annotations

import json
from pathlib import Path

from sourcefolio.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"


def test_render_ui_cli_writes_self_contained_page(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    vault = tmp_path / "vault"
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

    assert main(["render-ui", "--archive-dir", str(archive), "--vault", str(vault)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["namespace"] == "ui"
    assert output["items"] == 3

    page = Path(output["output"])
    assert page == vault / "_generated" / "sourcefolio" / "ui" / "index.html"
    assert page.is_file()
    html = page.read_text(encoding="utf-8")
    assert '<script id="sourcefolio-data"' in html
    assert "src_" in html and "cap_" in html

    assert (
        main(
            [
                "render-ui",
                "--archive-dir",
                str(archive),
                "--vault",
                str(vault),
                "--limit",
                "1",
            ]
        )
        == 0
    )
    limited = json.loads(capsys.readouterr().out)
    assert limited["items"] == 1
