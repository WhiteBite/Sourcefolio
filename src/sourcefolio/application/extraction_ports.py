from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import BinaryIO, Protocol

from pydantic import JsonValue

from sourcefolio.application.extraction_types import ExtractedText
from sourcefolio.domain.common import Provenance
from sourcefolio.domain.extraction import (
    EvidenceLocator,
    ExtractionKind,
    ExtractionRecord,
    ExtractionRequest,
)
from sourcefolio.domain.source import ArtifactRef, CaptureId


class TextExtractionAdapter(Protocol):
    @property
    def provenance(self) -> Provenance: ...

    @property
    def config(self) -> Mapping[str, JsonValue]: ...

    @property
    def kind(self) -> ExtractionKind: ...

    def prepare(self) -> None:
        """Resolve tool/model version before the request cache key is computed."""
        ...

    def extract(
        self,
        *,
        artifact: ArtifactRef,
        content: BinaryIO,
        locator: EvidenceLocator,
    ) -> ExtractedText: ...


class ExtractionRepository(Protocol):
    def save(self, record: ExtractionRecord) -> bool: ...

    def find_by_request(self, request: ExtractionRequest) -> ExtractionRecord | None: ...

    def iter_for_capture(self, capture_id: CaptureId) -> Iterator[ExtractionRecord]: ...
