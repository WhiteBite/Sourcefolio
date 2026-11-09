from __future__ import annotations

from sourcefolio.application.commands import (
    ArchiveCaptureCommand,
    ArchiveCaptureResult,
)
from sourcefolio.application.ports import ArtifactStore, SourcePacketRepository
from sourcefolio.domain.source import SourcePacket


class ArchiveCapture:
    """Archive raw evidence only; extraction and synthesis are separate use cases."""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        packets: SourcePacketRepository,
    ) -> None:
        self._artifacts = artifacts
        self._packets = packets

    def handle(self, command: ArchiveCaptureCommand) -> ArchiveCaptureResult:
        if not command.artifacts:
            raise ValueError("a capture requires at least one raw artifact")

        artifact_refs = tuple(self._artifacts.put(payload) for payload in command.artifacts)
        packet = SourcePacket.create(
            source=command.source,
            captured_at=command.captured_at,
            collector=command.collector,
            artifacts=artifact_refs,
            status=command.status,
            title=command.title,
            collections=command.collections,
            limitations=command.limitations,
            metadata=dict(command.metadata),
        )
        created = self._packets.save(packet)
        return ArchiveCaptureResult(packet=packet, created=created)
