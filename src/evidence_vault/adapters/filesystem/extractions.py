from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from evidence_vault.adapters.filesystem.atomic import write_immutable_bytes
from evidence_vault.domain.common import canonical_json
from evidence_vault.domain.extraction import ExtractionRecord, ExtractionRequest
from evidence_vault.domain.source import CaptureId


class FileSystemExtractionRepository:
    def __init__(self, derived_root: Path) -> None:
        self._root = derived_root

    def _capture_root(self, capture_id: CaptureId) -> Path:
        return self._root / "extractions" / capture_id

    def _path(self, record: ExtractionRecord) -> Path:
        return self._capture_root(record.request.capture_id) / f"{record.extraction_id}.json"

    def save(self, record: ExtractionRecord) -> bool:
        record = ExtractionRecord.model_validate(record.model_dump())
        data = (canonical_json(record) + "\n").encode("utf-8")
        return write_immutable_bytes(self._path(record), data)

    def find_by_request(self, request: ExtractionRequest) -> ExtractionRecord | None:
        matches = [
            record
            for record in self.iter_for_capture(request.capture_id)
            if record.request_id == request.request_id and record.request == request
        ]
        if not matches:
            return None
        matches.sort(key=lambda record: (record.created_at, record.extraction_id))
        return matches[-1]

    def iter_for_capture(self, capture_id: CaptureId) -> Iterator[ExtractionRecord]:
        root = self._capture_root(capture_id)
        if not root.exists():
            return
        for path in sorted(root.glob("ext_*.json")):
            record = ExtractionRecord.model_validate_json(path.read_bytes())
            if path != self._path(record):
                raise ValueError("extraction record path does not match its identifiers")
            yield record
