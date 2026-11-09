from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sourcefolio.application.extraction_ports import (
    ExtractionRepository,
    TextExtractionAdapter,
)
from sourcefolio.application.extraction_types import ExtractionResult
from sourcefolio.application.ports import ArtifactStore
from sourcefolio.domain.extraction import EvidenceLocator, ExtractionRecord, ExtractionRequest
from sourcefolio.domain.source import ArtifactRef, SourcePacket


class ExtractText:
    """Idempotent derived-text use case; raw captures remain unchanged."""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        extractions: ExtractionRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._extractions = extractions
        self._clock = clock or (lambda: datetime.now(UTC))

    def handle(
        self,
        *,
        packet: SourcePacket,
        artifact: ArtifactRef,
        locator: EvidenceLocator,
        adapter: TextExtractionAdapter,
    ) -> ExtractionResult:
        if artifact.artifact_id not in {item.artifact_id for item in packet.artifacts}:
            raise ValueError("extraction artifact does not belong to the source packet")
        adapter.prepare()
        request = ExtractionRequest(
            source_id=packet.source_id,
            capture_id=packet.capture_id,
            artifact_id=artifact.artifact_id,
            kind=adapter.kind,
            locator=locator,
            extractor=adapter.provenance,
            config=dict(adapter.config),
        )
        existing = self._extractions.find_by_request(request)
        if existing is not None:
            return ExtractionResult(record=existing, created=False)

        with self._artifacts.open(artifact.artifact_id) as content:
            output = adapter.extract(
                artifact=artifact,
                content=content,
                locator=locator,
            )
        record = ExtractionRecord.create(
            request=request,
            created_at=self._clock(),
            text=output.text,
            limitations=output.limitations,
        )
        created = self._extractions.save(record)
        return ExtractionResult(record=record, created=created)
