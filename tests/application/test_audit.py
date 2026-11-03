from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.audit import ArchiveAudit
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import ArtifactRole, SourceIdentity, SourceKind, SourcePacket


def add_packet(root: Path, content: bytes = b"evidence") -> SourcePacket:
    use_case = ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    )
    return use_case.handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="test",
                kind=SourceKind.DOCUMENT,
                external_id="one",
            ),
            captured_at=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
            collector=Provenance(
                actor_kind=ActorKind.TOOL,
                actor_id="test",
                method="test",
            ),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.ATTACHMENT,
                    media_type="application/octet-stream",
                    stream=BytesIO(content),
                ),
            ),
        )
    ).packet


def audit(root: Path) -> ArchiveAudit:
    store = FileSystemArtifactStore(root)
    return ArchiveAudit(
        packets=FileSystemSourcePacketRepository(root),
        artifacts=store,
        inventory=store,
    )


def artifact_path(root: Path, packet: SourcePacket) -> Path:
    digest = packet.artifacts[0].sha256
    return root / "artifacts" / digest[:2] / digest


def test_audit_verifies_references_and_is_repeatable(tmp_path: Path) -> None:
    packet = add_packet(tmp_path)
    use_case = audit(tmp_path)

    first = use_case.run(verify_bytes=True)
    second = use_case.run(verify_bytes=True)

    assert first.ok and second.ok
    assert first.packets == 1
    assert first.artifact_references == 1
    assert first.unique_artifacts == 1
    assert first.valid_artifacts == second.valid_artifacts == 1
    assert artifact_path(tmp_path, packet).exists()


def test_audit_detects_corruption_and_missing_artifacts(tmp_path: Path) -> None:
    packet = add_packet(tmp_path)
    path = artifact_path(tmp_path, packet)
    path.write_bytes(b"corrupt")

    corrupted = audit(tmp_path).run(verify_bytes=True)
    assert not corrupted.ok
    assert corrupted.issue_count == 1
    assert corrupted.issues[0].kind == "integrity_mismatch"

    path.unlink()
    missing = audit(tmp_path).run()
    assert not missing.ok
    assert missing.issues[0].kind == "artifact_unavailable"


def test_audit_reports_orphans_without_deleting_or_failing(tmp_path: Path) -> None:
    add_packet(tmp_path)
    digest = hashlib.sha256(b"orphan").hexdigest()
    orphan = tmp_path / "artifacts" / digest[:2] / digest
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")

    report = audit(tmp_path).run(verify_bytes=True)

    assert report.ok
    assert report.orphan_count == 1
    assert report.orphan_artifacts == (f"art_{digest}",)
    assert orphan.exists()
