from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import yaml

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemHumanNoteStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.dashboards import TriageDashboardProjection
from evidence_vault.application.triage import IdeaScaffolder
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def add_packet(
    root: Path,
    external_id: str,
    *,
    collections: tuple[str, ...],
    title: str,
) -> None:
    ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    ).handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="tiktok",
                kind=SourceKind.VIDEO,
                external_id=external_id,
                canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
            ),
            captured_at=NOW,
            collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
            title=title,
            collections=tuple(CollectionMembership(name=name) for name in collections),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain",
                    stream=BytesIO(b"caption"),
                ),
            ),
        )
    )


def test_idea_scaffolder_creates_then_skips(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    add_packet(archive, "1", collections=("Нейронки",), title="Первый")
    add_packet(archive, "2", collections=("Идеи",), title="Второй")
    vault = tmp_path / "vault"
    packets = FileSystemSourcePacketRepository(archive)
    scaffolder = IdeaScaffolder(
        packets=packets,
        notes=FileSystemHumanNoteStore(vault / "ideas"),
    )

    first = scaffolder.scaffold()
    assert first.created == 2
    assert first.skipped == 0
    assert len(list((vault / "ideas").glob("*.md"))) == 2

    source_id = next(iter(packets.iter_packets())).source_id
    idea = vault / "ideas" / f"{source_id}.md"
    content = idea.read_text(encoding="utf-8")
    properties = yaml.safe_load(content.split("---", 2)[1])
    assert properties["status"] == "inbox"
    assert properties["source_id"] == source_id
    assert "## Заметки" in content

    idea.write_text("human edit", encoding="utf-8")
    second = scaffolder.scaffold()
    assert second.created == 0
    assert second.skipped == 2
    assert idea.read_text(encoding="utf-8") == "human edit"


def test_triage_dashboards_generated(tmp_path: Path) -> None:
    generated = tmp_path / "vault" / "_generated" / "evidence-vault"
    store = FileSystemProjectionStore(generated)

    assert TriageDashboardProjection(store=store).rebuild() == 4

    inbox = (generated / "dashboards" / "inbox.md").read_text(encoding="utf-8")
    assert "Dataview" in inbox
    assert 'TABLE title, collections FROM "ideas" WHERE status = "inbox"' in inbox
    for status in ("keep", "drop", "done"):
        content = (generated / "dashboards" / f"{status}.md").read_text(encoding="utf-8")
        assert f'WHERE status = "{status}"' in content
