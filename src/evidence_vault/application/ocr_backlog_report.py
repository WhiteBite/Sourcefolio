from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from evidence_vault.application.extraction_ports import TextExtractionAdapter
from evidence_vault.application.ocr_backlog import OcrBacklogQuery, OcrBacklogReport
from evidence_vault.domain.common import ContractModel, NonEmptyStr, Provenance, UtcDateTime
from evidence_vault.domain.extraction import ExtractionId, ExtractionRequestId
from evidence_vault.domain.source import ArtifactId, CaptureId, CaptureStatus, SourceId, SourceKind


class OcrBacklogQueryDocument(ContractModel):
    capture_limit: int = Field(ge=1)
    slide_limit: int = Field(ge=1)
    capture_id: CaptureId | None = None
    collection: str | None = None
    kind: SourceKind | None = None
    status: CaptureStatus | None = None
    latest_only: bool


class OcrBacklogSelectionDocument(ContractModel):
    matched_captures: int = Field(ge=0)
    selected_captures: int = Field(ge=0)
    total_images: int = Field(ge=0)
    selected_images: int = Field(ge=0)


class OcrBacklogCountsDocument(ContractModel):
    created: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)


class OcrDurationDocument(ContractModel):
    count: int = Field(ge=0)
    total: float = Field(ge=0)
    mean: float = Field(ge=0)
    p50: float = Field(ge=0)
    p95: float = Field(ge=0)
    max: float = Field(ge=0)


class OcrBacklogItemDocument(ContractModel):
    source_id: SourceId
    capture_id: CaptureId
    artifact_id: ArtifactId
    slide: int = Field(ge=1)
    status: Literal["created", "skipped", "failed"]
    duration_seconds: float = Field(ge=0)
    request_id: ExtractionRequestId | None = None
    extraction_id: ExtractionId | None = None
    error: NonEmptyStr | None = None
    limitations: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def outcome_fields_match(self) -> OcrBacklogItemDocument:
        if self.status == "failed":
            if self.error is None:
                raise ValueError("failed OCR item requires an error")
            if self.request_id is not None or self.extraction_id is not None:
                raise ValueError("failed OCR item cannot expose a successful record")
        else:
            if self.error is not None:
                raise ValueError("successful OCR item cannot have an error")
            if self.request_id is None or self.extraction_id is None:
                raise ValueError("successful OCR item requires request and extraction IDs")
        return self


class OcrBacklogReportDocument(ContractModel):
    schema_version: Literal["ocr-backlog-report/v1"] = "ocr-backlog-report/v1"
    operation: Literal["ocr-capture", "ocr-backlog"]
    started_at: UtcDateTime
    finished_at: UtcDateTime
    query: OcrBacklogQueryDocument
    concurrency: int = Field(ge=1)
    adapter_prepared: bool
    extractor: Provenance
    extractor_config: dict[str, JsonValue] = Field(default_factory=dict)
    runtime: dict[str, JsonValue] = Field(default_factory=dict)
    selection: OcrBacklogSelectionDocument
    counts: OcrBacklogCountsDocument
    wall_seconds: float = Field(ge=0)
    item_duration_seconds: OcrDurationDocument
    items: tuple[OcrBacklogItemDocument, ...] = ()
    limitations: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def report_is_internally_consistent(self) -> OcrBacklogReportDocument:
        if self.finished_at < self.started_at:
            raise ValueError("report finish cannot precede its start")
        if self.selection.selected_images != len(self.items):
            raise ValueError("selected_images does not match report items")
        if self.item_duration_seconds.count != len(self.items):
            raise ValueError("duration count does not match report items")
        counts = OcrBacklogCountsDocument(
            created=sum(item.status == "created" for item in self.items),
            skipped=sum(item.status == "skipped" for item in self.items),
            failed=sum(item.status == "failed" for item in self.items),
        )
        if self.counts != counts:
            raise ValueError("report counts do not match report items")
        return self


def build_ocr_backlog_report(
    *,
    operation: Literal["ocr-capture", "ocr-backlog"],
    query: OcrBacklogQuery,
    report: OcrBacklogReport,
    adapter: TextExtractionAdapter,
    runtime: dict[str, JsonValue] | None = None,
) -> OcrBacklogReportDocument:
    limitations: list[str] = []
    if not report.adapter_prepared:
        limitations.append("No OCR work was selected; extractor version was not resolved.")
    if report.failed:
        limitations.append("Failed items are not cached as successful extraction records.")
    config = dict(adapter.config)
    if config.get("language_data_sha256") == "unverified":
        limitations.append("Tesseract language data fingerprint is unavailable.")
    duration = report.item_duration
    return OcrBacklogReportDocument(
        operation=operation,
        started_at=report.started_at,
        finished_at=report.finished_at,
        query=OcrBacklogQueryDocument(
            capture_limit=query.capture_limit,
            slide_limit=query.slide_limit,
            capture_id=query.capture_id,
            collection=query.collection,
            kind=query.kind,
            status=query.status,
            latest_only=query.latest_only,
        ),
        concurrency=report.concurrency,
        adapter_prepared=report.adapter_prepared,
        extractor=adapter.provenance,
        extractor_config=config,
        runtime=runtime or {},
        selection=OcrBacklogSelectionDocument(
            matched_captures=report.matched_captures,
            selected_captures=report.selected_captures,
            total_images=report.total_images,
            selected_images=report.selected_images,
        ),
        counts=OcrBacklogCountsDocument(
            created=report.created,
            skipped=report.skipped,
            failed=report.failed,
        ),
        wall_seconds=report.wall_seconds,
        item_duration_seconds=OcrDurationDocument(
            count=duration.count,
            total=duration.total_seconds,
            mean=duration.mean_seconds,
            p50=duration.p50_seconds,
            p95=duration.p95_seconds,
            max=duration.max_seconds,
        ),
        items=tuple(
            OcrBacklogItemDocument(
                source_id=item.source_id,
                capture_id=item.capture_id,
                artifact_id=item.artifact_id,
                slide=item.slide,
                status=item.status,
                duration_seconds=item.duration_seconds,
                request_id=item.record.request_id if item.record else None,
                extraction_id=item.record.extraction_id if item.record else None,
                error=item.error,
                limitations=item.record.limitations if item.record else (),
            )
            for item in report.results
        ),
        limitations=tuple(limitations),
    )
