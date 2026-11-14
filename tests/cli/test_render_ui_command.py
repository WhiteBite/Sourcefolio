from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio import cli
from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.cli import main
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

EXAMPLE = Path(__file__).parents[2] / "examples" / "tiktok-export" / "user_data.json"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
DESCRIPTION = "Alpha Tool is a useful library for working with text as data"


class FakeChatClient:
    def __init__(self, config: object = None) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return json.dumps(
            {
                "verdict": "candidate",
                "reason": "ok",
                "mentions": [
                    {
                        "name": "Alpha Tool",
                        "kind": "tool",
                        "fit": "high",
                        "quote": "Alpha Tool",
                        "field": "description",
                        "comment_index": None,
                    }
                ],
            },
            ensure_ascii=False,
        )


def add_packet(root: Path) -> tuple[str, str]:
    packet = (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="tiktok",
                    kind=SourceKind.VIDEO,
                    external_id="one",
                    canonical_url="https://www.tiktok.com/@x/video/one",
                ),
                captured_at=NOW,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                collections=(CollectionMembership(name="Нейронки"),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption"),
                    ),
                ),
                metadata={"saved_index": {"desc": DESCRIPTION, "author": "author"}},
            )
        )
        .packet
    )
    return packet.source_id, packet.capture_id


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


def test_render_ui_registry_note_links_source_cards_with_generated_prefix(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    archive = tmp_path / "archive"
    derived = tmp_path / "derived"
    vault = tmp_path / "vault"
    projects = tmp_path / "projects.json"
    source_id, capture_id = add_packet(archive)
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {"id": "sourcefolio", "name": "Sourcefolio", "description": "evidence archive"}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "OpenAIChatClient", FakeChatClient)

    assert (
        main(
            [
                "registry-extract",
                "--collection",
                "Нейронки",
                "--projects",
                str(projects),
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
                "--api-key",
                "test-key",
                "--model",
                "test-model",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "registry-build",
                "--projects",
                str(projects),
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "render-ui",
                "--archive-dir",
                str(archive),
                "--derived-dir",
                str(derived),
                "--vault",
                str(vault),
            ]
        )
        == 0
    )
    capsys.readouterr()

    project_note = (vault / "_generated" / "sourcefolio" / "registry" / "sourcefolio.md").read_text(
        encoding="utf-8"
    )
    assert f"[[_generated/sourcefolio/source-cards/{source_id}/{capture_id}|" in project_note
