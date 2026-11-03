from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from evidence_vault.application.extraction_ports import ExtractionRepository
from evidence_vault.application.ocr_backlog_report import (
    OcrBacklogItemDocument,
    OcrBacklogReportDocument,
)
from evidence_vault.domain.common import (
    ContractModel,
    NonEmptyStr,
    Provenance,
    UtcDateTime,
    canonical_json,
)
from evidence_vault.domain.extraction import (
    EvidenceLocator,
    ExtractionId,
    ExtractionKind,
    ExtractionRequestId,
)
from evidence_vault.domain.source import ArtifactId, CaptureId, Sha256

NORMALIZATION_VERSION: Literal["nfkc-casefold-whitespace/v1"] = "nfkc-casefold-whitespace/v1"


class OcrEvaluationSample(ContractModel):
    sample_id: NonEmptyStr
    capture_id: CaptureId
    artifact_id: ArtifactId
    locator: EvidenceLocator
    expected_text: str
    request_id: ExtractionRequestId | None = None
    notes: NonEmptyStr | None = None


class OcrEvaluationSet(ContractModel):
    schema_version: Literal["ocr-evaluation/v1"] = "ocr-evaluation/v1"
    samples: tuple[OcrEvaluationSample, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def sample_ids_are_unique(self) -> OcrEvaluationSet:
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("OCR evaluation sample_id values must be unique")
        return self


class OcrEvaluationCase(ContractModel):
    sample_id: NonEmptyStr
    status: Literal["evaluated", "failed", "missing", "oversized"]
    capture_id: CaptureId
    artifact_id: ArtifactId
    locator: EvidenceLocator
    request_id: ExtractionRequestId | None = None
    extraction_id: ExtractionId | None = None
    extractor: Provenance | None = None
    extractor_config: dict[str, JsonValue] = Field(default_factory=dict)
    available_records: int = Field(ge=0)
    normalized_expected_sha256: Sha256
    normalized_actual_sha256: Sha256 | None = None
    expected_excerpt: str
    actual_excerpt: str | None = None
    expected_characters: int = Field(ge=0)
    actual_characters: int | None = Field(default=None, ge=0)
    expected_words: int = Field(ge=0)
    actual_words: int | None = Field(default=None, ge=0)
    character_errors: int | None = Field(default=None, ge=0)
    word_errors: int | None = Field(default=None, ge=0)
    cer: float | None = Field(default=None, ge=0)
    wer: float | None = Field(default=None, ge=0)
    normalized_exact: bool | None = None
    empty_actual: bool | None = None
    extraction_limitations: tuple[NonEmptyStr, ...] = ()
    notes: NonEmptyStr | None = None
    error: NonEmptyStr | None = None

    @model_validator(mode="after")
    def metrics_match_status(self) -> OcrEvaluationCase:
        if self.status == "evaluated":
            if (
                self.extraction_id is None
                or self.request_id is None
                or self.cer is None
                or self.wer is None
                or self.normalized_exact is None
                or self.empty_actual is None
            ):
                raise ValueError("evaluated OCR case requires a record and metrics")
        elif self.cer is not None or self.wer is not None:
            raise ValueError("unresolved OCR case cannot have error rates")
        if self.status in {"failed", "oversized"} and self.error is None:
            raise ValueError("failed/oversized OCR case requires a detail")
        return self


class OcrEvaluationSummary(ContractModel):
    sample_count: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    failed: int = Field(ge=0)
    missing: int = Field(ge=0)
    oversized: int = Field(ge=0)
    empty_actual: int = Field(ge=0)
    exact_matches: int = Field(ge=0)
    multiple_record_samples: int = Field(ge=0)
    exact_match_rate: float = Field(ge=0, le=1)
    empty_actual_rate: float = Field(ge=0, le=1)
    failed_rate: float = Field(ge=0, le=1)
    missing_rate: float = Field(ge=0, le=1)
    mean_cer: float = Field(ge=0)
    mean_wer: float = Field(ge=0)


class OcrExtractorEvaluationSummary(ContractModel):
    extractor: Provenance
    extractor_config: dict[str, JsonValue] = Field(default_factory=dict)
    evaluated: int = Field(ge=1)
    empty_actual: int = Field(ge=0)
    exact_match_rate: float = Field(ge=0, le=1)
    mean_cer: float = Field(ge=0)
    mean_wer: float = Field(ge=0)


class OcrEvaluationReport(ContractModel):
    schema_version: Literal["ocr-evaluation-report/v1"] = "ocr-evaluation-report/v1"
    generated_at: UtcDateTime
    normalization: Literal["nfkc-casefold-whitespace/v1"] = NORMALIZATION_VERSION
    max_text_chars: int = Field(ge=1)
    batch_report_sha256: tuple[Sha256, ...] = ()
    summary: OcrEvaluationSummary
    extractors: tuple[OcrExtractorEvaluationSummary, ...] = ()
    cases: tuple[OcrEvaluationCase, ...]
    limitations: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def summary_matches_cases(self) -> OcrEvaluationReport:
        if self.summary.sample_count != len(self.cases):
            raise ValueError("evaluation sample count does not match cases")
        for status in ("evaluated", "failed", "missing", "oversized"):
            actual = sum(case.status == status for case in self.cases)
            if getattr(self.summary, status) != actual:
                raise ValueError(f"evaluation {status} count does not match cases")
        return self


def normalize_ocr_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _distance(left: Sequence[str], right: Sequence[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class OcrEvaluator:
    def __init__(
        self,
        *,
        extractions: ExtractionRepository,
        max_text_chars: int = 20_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_text_chars < 1:
            raise ValueError("max_text_chars must be at least 1")
        self._extractions = extractions
        self._max_text_chars = max_text_chars
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        evaluation_set: OcrEvaluationSet,
        *,
        batch_reports: Sequence[OcrBacklogReportDocument] = (),
    ) -> OcrEvaluationReport:
        outcomes: dict[
            tuple[CaptureId, ArtifactId, int],
            OcrBacklogItemDocument,
        ] = {}
        for batch in sorted(batch_reports, key=lambda item: item.finished_at):
            for item in batch.items:
                outcomes[(item.capture_id, item.artifact_id, item.slide)] = item
        failures = {key: item for key, item in outcomes.items() if item.status == "failed"}
        cases = tuple(self._evaluate_sample(sample, failures) for sample in evaluation_set.samples)
        evaluated = [case for case in cases if case.status == "evaluated"]
        summary = OcrEvaluationSummary(
            sample_count=len(cases),
            evaluated=len(evaluated),
            failed=sum(case.status == "failed" for case in cases),
            missing=sum(case.status == "missing" for case in cases),
            oversized=sum(case.status == "oversized" for case in cases),
            empty_actual=sum(bool(case.empty_actual) for case in evaluated),
            exact_matches=sum(bool(case.normalized_exact) for case in evaluated),
            multiple_record_samples=sum(case.available_records > 1 for case in cases),
            exact_match_rate=_rate(
                sum(bool(case.normalized_exact) for case in evaluated),
                len(evaluated),
            ),
            empty_actual_rate=_rate(
                sum(bool(case.empty_actual) for case in evaluated),
                len(evaluated),
            ),
            failed_rate=_rate(
                sum(case.status == "failed" for case in cases),
                len(cases),
            ),
            missing_rate=_rate(
                sum(case.status == "missing" for case in cases),
                len(cases),
            ),
            mean_cer=_mean([case.cer for case in evaluated if case.cer is not None]),
            mean_wer=_mean([case.wer for case in evaluated if case.wer is not None]),
        )
        limitations = [
            "CER/WER measure normalized text similarity, not factual correctness.",
        ]
        if summary.multiple_record_samples:
            limitations.append(
                "When request_id is absent, the latest matching record by "
                "created_at/extraction_id is evaluated."
            )
        if summary.missing and not batch_reports:
            limitations.append(
                "No batch reports were supplied; missing samples cannot be "
                "classified as explicit OCR failures."
            )
        if summary.oversized:
            limitations.append(
                "Oversized samples were not scored; increase max_text_chars "
                "deliberately after reviewing resource limits."
            )
        return OcrEvaluationReport(
            generated_at=self._clock(),
            max_text_chars=self._max_text_chars,
            batch_report_sha256=tuple(
                _sha256(canonical_json(report.model_dump(mode="json"))) for report in batch_reports
            ),
            summary=summary,
            extractors=self._extractor_summaries(evaluated),
            cases=cases,
            limitations=tuple(limitations),
        )

    def _evaluate_sample(
        self,
        sample: OcrEvaluationSample,
        failures: dict[tuple[CaptureId, ArtifactId, int], OcrBacklogItemDocument],
    ) -> OcrEvaluationCase:
        expected = normalize_ocr_text(sample.expected_text)
        expected_words = expected.split()
        matching = [
            record
            for record in self._extractions.iter_for_capture(sample.capture_id)
            if record.request.kind == ExtractionKind.OCR_TEXT
            and record.request.artifact_id == sample.artifact_id
            and record.request.locator == sample.locator
        ]
        candidates = [
            record
            for record in matching
            if sample.request_id is None or record.request_id == sample.request_id
        ]
        base = {
            "sample_id": sample.sample_id,
            "capture_id": sample.capture_id,
            "artifact_id": sample.artifact_id,
            "locator": sample.locator,
            "available_records": len(matching),
            "normalized_expected_sha256": _sha256(expected),
            "expected_excerpt": expected[:500],
            "expected_characters": len(expected),
            "expected_words": len(expected_words),
            "notes": sample.notes,
        }
        if not candidates:
            failure = None
            if (
                sample.request_id is None
                and sample.locator.kind.value == "slide"
                and sample.locator.slide is not None
            ):
                failure = failures.get(
                    (sample.capture_id, sample.artifact_id, sample.locator.slide)
                )
            if failure is not None:
                return OcrEvaluationCase(
                    **base,
                    status="failed",
                    error=failure.error or "OCR batch item failed",
                )
            return OcrEvaluationCase(**base, status="missing")

        record = max(candidates, key=lambda item: (item.created_at, item.extraction_id))
        actual = normalize_ocr_text(record.text)
        actual_words = actual.split()
        record_fields = {
            "request_id": record.request_id,
            "extraction_id": record.extraction_id,
            "extractor": record.request.extractor,
            "extractor_config": record.request.config,
            "normalized_actual_sha256": _sha256(actual),
            "actual_excerpt": record.excerpt,
            "actual_characters": len(actual),
            "actual_words": len(actual_words),
            "extraction_limitations": record.limitations,
        }
        if len(expected) > self._max_text_chars or len(actual) > self._max_text_chars:
            return OcrEvaluationCase(
                **base,
                **record_fields,
                status="oversized",
                error="normalized text exceeds max_text_chars",
            )
        character_errors = _distance(expected, actual)
        word_errors = _distance(expected_words, actual_words)
        return OcrEvaluationCase(
            **base,
            **record_fields,
            status="evaluated",
            character_errors=character_errors,
            word_errors=word_errors,
            cer=character_errors / max(1, len(expected)),
            wer=word_errors / max(1, len(expected_words)),
            normalized_exact=expected == actual,
            empty_actual=not actual,
        )

    @staticmethod
    def _extractor_summaries(
        cases: Sequence[OcrEvaluationCase],
    ) -> tuple[OcrExtractorEvaluationSummary, ...]:
        groups: dict[str, list[OcrEvaluationCase]] = {}
        for case in cases:
            if case.extractor is None:
                continue
            key = canonical_json(
                {
                    "extractor": case.extractor.model_dump(mode="json"),
                    "config": case.extractor_config,
                }
            )
            groups.setdefault(key, []).append(case)
        summaries = [
            OcrExtractorEvaluationSummary(
                extractor=group[0].extractor,
                extractor_config=group[0].extractor_config,
                evaluated=len(group),
                empty_actual=sum(bool(case.empty_actual) for case in group),
                exact_match_rate=_rate(
                    sum(bool(case.normalized_exact) for case in group),
                    len(group),
                ),
                mean_cer=_mean([case.cer for case in group if case.cer is not None]),
                mean_wer=_mean([case.wer for case in group if case.wer is not None]),
            )
            for _, group in sorted(groups.items())
        ]
        return tuple(summaries)
