from __future__ import annotations

from dataclasses import dataclass

from evidence_vault.domain.extraction import ExtractionRecord


class ExtractionError(Exception):
    """A safe extraction failure; failed work is not cached as a derived record."""


@dataclass(frozen=True)
class ExtractedText:
    text: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    record: ExtractionRecord
    created: bool
