from __future__ import annotations

import json
from pathlib import Path

from conftest import write_fake_executable
from evidence_vault.cli import main

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"


def fake_gallery_dl(path: Path) -> Path:
    return write_fake_executable(
        path,
        """import json
import sys
from pathlib import Path

if "--version" in sys.argv:
    print("fake-gallery-dl-cli")
    raise SystemExit(0)
directory = Path(sys.argv[sys.argv.index("--directory") + 1])
image = directory / "slide-01.jpg"
image.write_bytes(b"slide")
info = {
    "id": "7300000000000000002",
    "desc": "CLI slides",
    "num": 1,
    "imagePost": {"images": [{"imageURL": {"urlList": ["signed-secret"]}}]},
}
Path(str(image) + ".json").write_text(json.dumps(info), encoding="utf-8")
""",
    )


def test_tiktok_slides_cli_filters_tasks_and_resumes(tmp_path: Path, capsys) -> None:
    executable = fake_gallery_dl(tmp_path / "fake-gallery-dl")
    arguments = [
        "tiktok-slides",
        str(EXAMPLE),
        "--archive-dir",
        str(tmp_path / "archive"),
        "--control-db",
        str(tmp_path / "control.sqlite"),
        "--executable",
        str(executable),
        "--temp-dir",
        str(tmp_path / "tmp"),
        "--min-free-mb",
        "0",
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["unique_tasks"] == 3
    assert first["matching_tasks"] == 1
    assert first["submitted_tasks"] == 1
    assert first["results"]["succeeded"] == 1
    assert first["operation"].startswith("gallery-dl-tiktok-slides/v1:")

    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["results"]["skipped"] == 1
    packets = list((tmp_path / "archive" / "sources").rglob("cap_*.json"))
    assert len(packets) == 1
    packet = json.loads(packets[0].read_text(encoding="utf-8"))
    assert packet["source"]["kind"] == "slideshow"
    assert packet["status"] == "complete"
