from __future__ import annotations

import json
from pathlib import Path

from conftest import write_fake_executable
from evidence_vault.cli import main


def test_tiktok_download_command_uses_media_operation_and_resume(
    tmp_path: Path,
    capsys,
) -> None:
    export = tmp_path / "user_data.json"
    export.write_text(
        json.dumps(
            {
                "Activity": {
                    "Favorite Videos": {
                        "FavoriteVideoList": [
                            {
                                "Date": "2026-11-01T12:00:00Z",
                                "Link": "https://www.tiktok.com/@example/video/123",
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    fake = write_fake_executable(
        tmp_path / "fake-yt-dlp",
        """import json
import sys
from pathlib import Path
if "--version" in sys.argv:
    print("fake-cli-yt-dlp")
    raise SystemExit(0)
directory = Path(sys.argv[sys.argv.index("--paths") + 1])
(directory / "source.mp4").write_bytes(b"cli-video")
(directory / "source.info.json").write_text(json.dumps({
    "id": "123",
    "title": "CLI video",
    "webpage_url": sys.argv[-1]
}), encoding="utf-8")
""",
    )
    archive = tmp_path / "archive"
    arguments = [
        "tiktok-download",
        str(export),
        "--executable",
        str(fake),
        "--archive-dir",
        str(archive),
        "--control-db",
        str(tmp_path / "control.sqlite"),
        "--temp-dir",
        str(tmp_path / "tmp"),
        "--min-free-mb",
        "0",
        "--concurrency",
        "1",
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["operation"].startswith("yt-dlp-tiktok/v1:")
    assert first["results"]["succeeded"] == 1
    assert second["results"]["skipped"] == 1
    assert len(list((archive / "sources").rglob("*.json"))) == 1
    assert list((tmp_path / "tmp").iterdir()) == []
