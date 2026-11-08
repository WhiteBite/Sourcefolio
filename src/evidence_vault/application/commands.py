from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import IO

from pydantic import JsonValue

from evidence_vault.domain.common import Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CaptureStatus,
    CollectionMembership,
    SourceIdentity,
    SourcePacket,
)


@dataclass(frozen=True)
class ArtifactPayload:
    """A one-shot readable stream; the caller retains ownership and closes it."""

    role: ArtifactRole
    media_type: str
    stream: IO[bytes]
    original_filename: str | None = None


@dataclass(frozen=True)
class ArchiveCaptureCommand:
    source: SourceIdentity
    captured_at: datetime
    collector: Provenance
    artifacts: tuple[ArtifactPayload, ...]
    status: CaptureStatus = CaptureStatus.COMPLETE
    title: str | None = None
    collections: tuple[CollectionMembership, ...] = ()
    limitations: tuple[str, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchiveCaptureResult:
    packet: SourcePacket
    created: bool
