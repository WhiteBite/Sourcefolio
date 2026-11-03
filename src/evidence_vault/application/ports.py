from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO, Protocol

from evidence_vault.application.commands import ArtifactPayload
from evidence_vault.domain.source import (
    ArtifactId,
    ArtifactRef,
    CaptureId,
    SourceId,
    SourcePacket,
)


class ArtifactStore(Protocol):
    def put(self, payload: ArtifactPayload) -> ArtifactRef:
        """Persist a stream without replacing existing content."""
        ...

    def open(self, artifact_id: ArtifactId) -> BinaryIO:
        """Open stored bytes for reading; the caller closes the stream."""
        ...

    def verify(self, artifact_id: ArtifactId) -> bool:
        """Return whether the current bytes still match the content address."""
        ...


class ArtifactInventory(Protocol):
    def iter_artifact_ids(self) -> Iterator[ArtifactId]:
        """Iterate stored content addresses for read-only reconciliation."""
        ...


class ArtifactLocator(Protocol):
    def uri(self, artifact_id: ArtifactId) -> str:
        """Return a local or remote URI without exposing storage internals in the use case."""
        ...


class SourcePacketRepository(Protocol):
    def save(self, packet: SourcePacket) -> bool:
        """Return True if created, False if the identical packet already existed."""
        ...

    def get(self, source_id: SourceId, capture_id: CaptureId) -> SourcePacket | None: ...

    def iter_packets(self) -> Iterator[SourcePacket]:
        """Iterate canonical packets without loading the whole archive at once."""
        ...
