from __future__ import annotations

from dataclasses import dataclass

from sourcefolio.application.ports import (
    ArtifactInventory,
    ArtifactStore,
    SourcePacketRepository,
)
from sourcefolio.domain.source import ArtifactId, CaptureId, SourceId


@dataclass(frozen=True)
class AuditIssue:
    kind: str
    artifact_id: ArtifactId
    source_id: SourceId
    capture_id: CaptureId
    detail: str


@dataclass(frozen=True)
class ArchiveAuditReport:
    packets: int
    artifact_references: int
    unique_artifacts: int
    valid_artifacts: int
    verify_bytes: bool
    issue_count: int
    issues: tuple[AuditIssue, ...]
    issues_truncated: bool
    orphan_count: int
    orphan_artifacts: tuple[ArtifactId, ...]
    orphans_truncated: bool

    @property
    def ok(self) -> bool:
        return self.issue_count == 0


class ArchiveAudit:
    """Read-only reconciliation; orphan artifacts are reported, never deleted."""

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactStore,
        inventory: ArtifactInventory | None = None,
        max_reported_issues: int = 100,
    ) -> None:
        if max_reported_issues < 1:
            raise ValueError("max_reported_issues must be at least 1")
        self._packets = packets
        self._artifacts = artifacts
        self._inventory = inventory
        self._max_reported = max_reported_issues

    def run(self, *, verify_bytes: bool = False) -> ArchiveAuditReport:
        packet_count = 0
        reference_count = 0
        valid_artifacts = 0
        referenced: dict[ArtifactId, tuple[SourceId, CaptureId]] = {}
        checked: set[ArtifactId] = set()
        issues: list[AuditIssue] = []
        issue_count = 0

        for packet in self._packets.iter_packets():
            packet_count += 1
            reference_count += len(packet.artifacts)
            for artifact in packet.artifacts:
                artifact_id = artifact.artifact_id
                referenced.setdefault(artifact_id, (packet.source_id, packet.capture_id))
                if artifact_id in checked:
                    continue
                checked.add(artifact_id)
                source_id, capture_id = referenced[artifact_id]
                issue: AuditIssue | None = None
                try:
                    if verify_bytes:
                        if not self._artifacts.verify(artifact_id):
                            issue = AuditIssue(
                                kind="integrity_mismatch",
                                artifact_id=artifact_id,
                                source_id=source_id,
                                capture_id=capture_id,
                                detail="artifact bytes do not match their content address",
                            )
                    else:
                        with self._artifacts.open(artifact_id):
                            pass
                except Exception as exc:
                    issue = AuditIssue(
                        kind="artifact_unavailable",
                        artifact_id=artifact_id,
                        source_id=source_id,
                        capture_id=capture_id,
                        detail=type(exc).__name__,
                    )
                if issue is None:
                    valid_artifacts += 1
                else:
                    issue_count += 1
                    if len(issues) < self._max_reported:
                        issues.append(issue)

        orphan_count = 0
        orphans: list[ArtifactId] = []
        if self._inventory is not None:
            for artifact_id in self._inventory.iter_artifact_ids():
                if artifact_id not in referenced:
                    orphan_count += 1
                    if len(orphans) < self._max_reported:
                        orphans.append(artifact_id)

        return ArchiveAuditReport(
            packets=packet_count,
            artifact_references=reference_count,
            unique_artifacts=len(referenced),
            valid_artifacts=valid_artifacts,
            verify_bytes=verify_bytes,
            issue_count=issue_count,
            issues=tuple(issues),
            issues_truncated=issue_count > len(issues),
            orphan_count=orphan_count,
            orphan_artifacts=tuple(orphans),
            orphans_truncated=orphan_count > len(orphans),
        )
