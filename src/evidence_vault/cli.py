from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, ValidationError

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemHumanNoteStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.atomic import ImmutableStoreConflict, write_immutable_bytes
from evidence_vault.adapters.filesystem.extractions import FileSystemExtractionRepository
from evidence_vault.adapters.filesystem.llm_cache import FileSystemLlmCache
from evidence_vault.adapters.filesystem.projections import FileSystemProjectionStore
from evidence_vault.adapters.github import GitHubRepoClient, GitHubRepoConfig
from evidence_vault.adapters.llm import OpenAIChatClient, OpenAIChatConfig
from evidence_vault.adapters.metadata_only import MetadataOnlyCaptureAdapter
from evidence_vault.adapters.ocr.tesseract import TesseractConfig, TesseractOcrAdapter
from evidence_vault.adapters.sqlite import SQLiteCaptureState
from evidence_vault.adapters.tiktok import CollectionCaptureParser, TikTokExportParser
from evidence_vault.adapters.tiktok.collections import CollectionCaptureDocument
from evidence_vault.adapters.tiktok.export import ExportParseResult
from evidence_vault.adapters.tiktok.gallery_dl import (
    GalleryDlConfig,
    GalleryDlTikTokSlidesAdapter,
)
from evidence_vault.adapters.tiktok.slides_images import (
    SlidesImageConfig,
    SlidesImageFetcher,
)
from evidence_vault.adapters.tiktok.yt_dlp import YtDlpConfig, YtDlpTikTokAdapter
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.audit import ArchiveAudit
from evidence_vault.application.capture_batch import CaptureBatchRunner
from evidence_vault.application.capture_ports import CaptureAdapter
from evidence_vault.application.capture_report import (
    CaptureReportBuilder,
    render_capture_report_markdown,
)
from evidence_vault.application.capture_types import BatchItemStatus, RetryPolicy
from evidence_vault.application.collection_index import CollectionIndexProjection
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.dashboards import TriageDashboardProjection
from evidence_vault.application.evidence_pack import (
    EvidencePackBuilder,
    EvidencePackDocument,
    evidence_pack_to_dict,
    render_evidence_pack_markdown,
)
from evidence_vault.application.evidence_pack_integrity import (
    EvidencePackIntegrityReport,
    EvidencePackVerifier,
)
from evidence_vault.application.llm_catalog import LlmCatalogQuery, LlmCatalogRunner
from evidence_vault.application.llm_enrich import LlmEnrichQuery, LlmEnrichRunner
from evidence_vault.application.obsidian_views import ObsidianOverviewProjection
from evidence_vault.application.ocr_backlog import OcrBacklogQuery, OcrBacklogRunner
from evidence_vault.application.ocr_backlog_report import (
    OcrBacklogReportDocument,
    build_ocr_backlog_report,
)
from evidence_vault.application.ocr_evaluation import (
    OcrEvaluationReport,
    OcrEvaluationSet,
    OcrEvaluator,
)
from evidence_vault.application.project_fit import (
    Project,
    ProjectFitQuery,
    ProjectFitRunner,
    parse_projects,
)
from evidence_vault.application.repo_catalog import RepoCatalogQuery, RepoCatalogRunner
from evidence_vault.application.search import SearchIndexBuilder
from evidence_vault.application.search_evaluation import SearchEvaluationSet, SearchEvaluator
from evidence_vault.application.slides_fetch import FetchSlidesQuery, FetchSlidesRunner
from evidence_vault.application.source_cards import SourceCardProjection
from evidence_vault.application.triage import IdeaScaffolder
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.common import ContractModel, Provenance, UtcDateTime
from evidence_vault.domain.evidence import EvidenceItem
from evidence_vault.domain.extraction import ExtractionRecord
from evidence_vault.domain.source import (
    ArtifactRole,
    CaptureStatus,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)


class ArtifactManifest(ContractModel):
    path: str
    role: ArtifactRole
    media_type: str
    original_filename: str | None = None


class CaptureManifest(ContractModel):
    """Local manual-import input; artifact paths are not part of SourcePacket."""

    schema_version: Literal["capture-manifest/v1"]
    source: SourceIdentity
    captured_at: UtcDateTime
    collector: Provenance
    artifacts: tuple[ArtifactManifest, ...] = Field(min_length=1)
    status: CaptureStatus = CaptureStatus.COMPLETE
    title: str | None = None
    collections: tuple[CollectionMembership, ...] = ()
    limitations: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


DEFAULT_LLM_BASE_URL = "https://api.reformboss.com/v1"
DEFAULT_LLM_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"
DEFAULT_LLM_CONFIG_PATH = Path("data/llm.json")


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str


def _read_llm_config_file(path: Path) -> Mapping[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read LLM config file: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("LLM config file must contain a JSON object")
    return {key: value for key, value in data.items() if isinstance(value, str)}


def resolve_llm_config(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LlmConfig:
    environment = os.environ if environ is None else environ
    path = DEFAULT_LLM_CONFIG_PATH if config_path is None else config_path
    file_config = _read_llm_config_file(path)
    resolved_base_url = (
        base_url
        or environment.get("EV_LLM_BASE_URL")
        or file_config.get("base_url")
        or DEFAULT_LLM_BASE_URL
    )
    resolved_api_key = api_key or environment.get("EV_LLM_API_KEY") or file_config.get("api_key")
    resolved_model = (
        model or environment.get("EV_LLM_MODEL") or file_config.get("model") or DEFAULT_LLM_MODEL
    )
    if not resolved_api_key:
        raise ValueError(
            "LLM api_key is required: pass --api-key, set EV_LLM_API_KEY, or add data/llm.json"
        )
    return LlmConfig(base_url=resolved_base_url, api_key=resolved_api_key, model=resolved_model)


def _load_projects(path: Path) -> tuple[Project, ...]:
    if not path.is_file():
        raise ValueError(f"projects file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read projects file: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("projects file must contain a JSON object")
    return parse_projects(data)


def _archive_capture(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    manifest = CaptureManifest.model_validate_json(manifest_path.read_bytes())
    archive_root = Path(args.archive_dir).resolve()

    with ExitStack() as stack:
        payloads: list[ArtifactPayload] = []
        for artifact in manifest.artifacts:
            artifact_path = (manifest_path.parent / artifact.path).resolve()
            stream = stack.enter_context(artifact_path.open("rb"))
            payloads.append(
                ArtifactPayload(
                    role=artifact.role,
                    media_type=artifact.media_type,
                    stream=stream,
                    original_filename=artifact.original_filename or artifact_path.name,
                )
            )

        use_case = ArchiveCapture(
            artifacts=FileSystemArtifactStore(archive_root),
            packets=FileSystemSourcePacketRepository(archive_root),
        )
        result = use_case.handle(
            ArchiveCaptureCommand(
                source=manifest.source,
                captured_at=manifest.captured_at,
                collector=manifest.collector,
                artifacts=tuple(payloads),
                status=manifest.status,
                title=manifest.title,
                collections=manifest.collections,
                limitations=manifest.limitations,
                metadata=manifest.metadata,
            )
        )

    output = {
        "schema_version": result.packet.schema_version,
        "source_id": result.packet.source_id,
        "capture_id": result.packet.capture_id,
        "artifact_ids": [artifact.artifact_id for artifact in result.packet.artifacts],
        "created": result.created,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _archive_audit(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    artifacts = FileSystemArtifactStore(archive_root)
    report = ArchiveAudit(
        packets=FileSystemSourcePacketRepository(archive_root),
        artifacts=artifacts,
        inventory=artifacts,
        max_reported_issues=args.max_issues,
    ).run(verify_bytes=args.verify_bytes)
    output = {
        "ok": report.ok,
        "packets": report.packets,
        "artifact_references": report.artifact_references,
        "unique_artifacts": report.unique_artifacts,
        "valid_artifacts": report.valid_artifacts,
        "verify_bytes": report.verify_bytes,
        "issue_count": report.issue_count,
        "issues": [
            {
                "kind": issue.kind,
                "artifact_id": issue.artifact_id,
                "source_id": issue.source_id,
                "capture_id": issue.capture_id,
                "detail": issue.detail,
            }
            for issue in report.issues
        ],
        "issues_truncated": report.issues_truncated,
        "orphan_count": report.orphan_count,
        "orphan_artifacts": list(report.orphan_artifacts),
        "orphans_truncated": report.orphans_truncated,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def _capture_report(args: argparse.Namespace) -> int:
    with SQLiteCaptureState(Path(args.control_db).resolve()) as state:
        report = CaptureReportBuilder(
            state=state,
            stale_after_seconds=args.stale_after_seconds,
            recent_errors_limit=args.recent_errors,
        ).build()
    if args.report_format == "markdown":
        print(render_capture_report_markdown(report))
        return 0
    duration = report.attempt_duration
    output = {
        "generated_at": report.generated_at.isoformat(),
        "total_jobs": report.total_jobs,
        "status_counts": report.status_counts,
        "kind_status_counts": report.kind_status_counts,
        "total_attempts": report.total_attempts,
        "attempt_status_counts": report.attempt_status_counts,
        "jobs_with_retries": report.jobs_with_retries,
        "retry_attempts": report.retry_attempts,
        "stale_running_candidates": report.stale_running_candidates,
        "attempt_duration_seconds": {
            "count": duration.count,
            "total": duration.total_seconds,
            "mean": duration.mean_seconds,
            "p50": duration.p50_seconds,
            "p95": duration.p95_seconds,
            "max": duration.max_seconds,
        },
        "recent_errors": [
            {
                "task_id": error.task_id,
                "capture_id": error.capture_id,
                "external_id": error.external_id,
                "canonical_url": error.canonical_url,
                "kind": error.kind.value,
                "status": error.status.value,
                "error": error.error,
                "updated_at": error.updated_at.isoformat(),
            }
            for error in report.recent_errors
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _parse_tiktok_export(args: argparse.Namespace) -> ExportParseResult:
    if args.limit is not None and args.limit < 1:
        raise ValueError("limit must be at least 1")
    parser = TikTokExportParser(naive_date_timezone=args.export_timezone)
    return parser.parse(
        Path(args.export).resolve(),
        file_format=args.file_format,
    )


def _task_summary(task: CaptureTask) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "source_id": task.source_id,
        "external_id": task.source.external_id,
        "kind": task.source.kind.value,
        "canonical_url": task.source.canonical_url,
        "saved_at": task.saved_at.isoformat() if task.saved_at else None,
        "collections": [collection.name for collection in task.collections],
        "raw_entry_count": len(task.raw_entries),
    }


def _issues_and_warnings(parsed: ExportParseResult) -> dict[str, object]:
    return {
        "issues": [
            {"entry_index": issue.entry_index, "reason": issue.reason} for issue in parsed.issues
        ],
        "warnings": list(parsed.warnings),
    }


def _tiktok_inspect(args: argparse.Namespace) -> int:
    parsed = _parse_tiktok_export(args)
    limit = args.limit if args.limit is not None else 20
    output = {
        "entries_read": parsed.entries_read,
        "unique_tasks": len(parsed.tasks),
        "returned_tasks": min(limit, len(parsed.tasks)),
        "items": [_task_summary(task) for task in parsed.tasks[:limit]],
        **_issues_and_warnings(parsed),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _run_tiktok_batch(
    args: argparse.Namespace,
    adapter: CaptureAdapter,
    *,
    partial_is_failure: bool,
    allowed_kinds: frozenset[SourceKind] | None = None,
) -> int:
    parsed = _parse_tiktok_export(args)
    matching_tasks = (
        parsed.tasks
        if allowed_kinds is None
        else [task for task in parsed.tasks if task.source.kind in allowed_kinds]
    )
    tasks = matching_tasks[: args.limit] if args.limit is not None else matching_tasks
    archive_root = Path(args.archive_dir).resolve()
    control_path = Path(args.control_db).resolve()
    archive = ArchiveCapture(
        artifacts=FileSystemArtifactStore(archive_root),
        packets=FileSystemSourcePacketRepository(archive_root),
    )
    with SQLiteCaptureState(control_path) as state:
        runner = CaptureBatchRunner(
            adapter=adapter,
            archive=archive,
            state=state,
            concurrency=args.concurrency,
            retry_policy=RetryPolicy(max_attempts=args.max_attempts),
        )
        results = asyncio.run(runner.run(tasks))

    counts = {status.value: 0 for status in BatchItemStatus}
    for result in results:
        counts[result.status.value] += 1
    output = {
        "operation": adapter.operation_key,
        "entries_read": parsed.entries_read,
        "unique_tasks": len(parsed.tasks),
        "matching_tasks": len(matching_tasks),
        "submitted_tasks": len(tasks),
        "results": counts,
        "archive_dir": str(archive_root),
        "control_db": str(control_path),
        **_issues_and_warnings(parsed),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    incomplete = counts[BatchItemStatus.FAILED.value]
    if partial_is_failure:
        incomplete += counts[BatchItemStatus.PARTIAL.value]
    return 1 if incomplete else 0


def _tiktok_import(args: argparse.Namespace) -> int:
    return _run_tiktok_batch(
        args,
        MetadataOnlyCaptureAdapter(),
        partial_is_failure=False,
    )


def _tiktok_download(args: argparse.Namespace) -> int:
    adapter = YtDlpTikTokAdapter(
        YtDlpConfig(
            executable=args.executable,
            temp_root=Path(args.temp_dir).resolve(),
            format_selector=args.format_selector,
            site_limit=args.site_limit,
            total_timeout_seconds=args.timeout,
            socket_timeout_seconds=args.socket_timeout,
            max_artifact_bytes=args.max_file_mb * 1024**2,
            min_free_bytes=args.min_free_mb * 1024**2,
        )
    )
    return _run_tiktok_batch(args, adapter, partial_is_failure=True)


def _tiktok_slides(args: argparse.Namespace) -> int:
    adapter = GalleryDlTikTokSlidesAdapter(
        GalleryDlConfig(
            executable=args.executable,
            temp_root=Path(args.temp_dir).resolve(),
            site_limit=args.site_limit,
            total_timeout_seconds=args.timeout,
            socket_timeout_seconds=args.socket_timeout,
            max_slides=args.max_slides,
            max_slide_bytes=args.max_slide_mb * 1024**2,
            max_total_bytes=args.max_total_mb * 1024**2,
            min_free_bytes=args.min_free_mb * 1024**2,
        )
    )
    return _run_tiktok_batch(
        args,
        adapter,
        partial_is_failure=True,
        allowed_kinds=frozenset({SourceKind.SLIDESHOW}),
    )


def _import_collections(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise ValueError("limit must be at least 1")
    parsed = CollectionCaptureParser().parse(Path(args.file).resolve())
    tasks = parsed.tasks[: args.limit] if args.limit is not None else parsed.tasks
    archive_root = Path(args.archive_dir).resolve()
    control_path = Path(args.control_db).resolve()
    archive = ArchiveCapture(
        artifacts=FileSystemArtifactStore(archive_root),
        packets=FileSystemSourcePacketRepository(archive_root),
    )
    adapter = MetadataOnlyCaptureAdapter()
    with SQLiteCaptureState(control_path) as state:
        runner = CaptureBatchRunner(
            adapter=adapter,
            archive=archive,
            state=state,
            concurrency=args.concurrency,
        )
        results = asyncio.run(runner.run(tasks))

    counts = {status.value: 0 for status in BatchItemStatus}
    for result in results:
        counts[result.status.value] += 1
    output = {
        "operation": adapter.operation_key,
        "entries_read": parsed.entries_read,
        "unique_tasks": len(parsed.tasks),
        "submitted_tasks": len(tasks),
        "results": counts,
        "archive_dir": str(archive_root),
        "control_db": str(control_path),
        "issues": [
            {
                "collection_index": issue.collection_index,
                "item_index": issue.item_index,
                "reason": issue.reason,
            }
            for issue in parsed.issues
        ],
        "warnings": list(parsed.warnings),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if counts[BatchItemStatus.FAILED.value] else 0


def _fetch_slides(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    packets = FileSystemSourcePacketRepository(archive_root)
    report = FetchSlidesRunner(
        packets=packets,
        archive=ArchiveCapture(
            artifacts=FileSystemArtifactStore(archive_root),
            packets=packets,
        ),
        fetcher=SlidesImageFetcher(
            SlidesImageConfig(max_bytes=args.max_mb * 1024**2),
        ),
    ).run(
        FetchSlidesQuery(
            collection=args.collection,
            limit=args.limit,
            max_slides=args.max_slides,
        )
    )
    output = {
        "collection": report.collection,
        "sources": report.sources,
        "images_downloaded": report.images_downloaded,
        "failed": report.failed,
        "created": report.created,
        "skipped": report.skipped,
        "results": [
            {
                "source_id": item.source_id,
                "capture_id": item.capture_id,
                "status": item.status,
                "images_downloaded": item.images_downloaded,
                "failed_indexes": list(item.failed_indexes),
            }
            for item in report.results
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if report.failed else 0


def _ocr_adapter(args: argparse.Namespace) -> TesseractOcrAdapter:
    return TesseractOcrAdapter(
        TesseractConfig(
            executable=args.executable,
            temp_root=Path(args.temp_dir).resolve(),
            tessdata_dir=Path(args.tessdata_dir).resolve() if args.tessdata_dir else None,
            language=args.language,
            page_segmentation_mode=args.psm,
            timeout_seconds=args.timeout,
            max_input_bytes=args.max_input_mb * 1024**2,
            max_output_bytes=args.max_output_kb * 1024,
            min_free_bytes=args.min_free_mb * 1024**2,
        )
    )


def _run_ocr(args: argparse.Namespace, query: OcrBacklogQuery) -> int:
    archive_root = Path(args.archive_dir).resolve()
    adapter = _ocr_adapter(args)
    report = OcrBacklogRunner(
        packets=FileSystemSourcePacketRepository(archive_root),
        artifacts=FileSystemArtifactStore(archive_root),
        extractions=FileSystemExtractionRepository(Path(args.derived_dir).resolve()),
        adapter=adapter,
        concurrency=args.concurrency,
    ).run(query)
    duration = report.item_duration
    output = {
        "capture_id": query.capture_id,
        "matched_captures": report.matched_captures,
        "selected_captures": report.selected_captures,
        "total_images": report.total_images,
        "selected_images": report.selected_images,
        "results": {
            "created": report.created,
            "skipped": report.skipped,
            "failed": report.failed,
        },
        "wall_seconds": report.wall_seconds,
        "item_duration_seconds": {
            "count": duration.count,
            "total": duration.total_seconds,
            "mean": duration.mean_seconds,
            "p50": duration.p50_seconds,
            "p95": duration.p95_seconds,
            "max": duration.max_seconds,
        },
        "records": [
            {
                "source_id": item.source_id,
                "capture_id": item.capture_id,
                "artifact_id": item.artifact_id,
                "slide": item.slide,
                "created": item.status == "created",
                "extraction_id": item.record.extraction_id,
                "request_id": item.record.request_id,
                "excerpt": item.record.excerpt,
                "limitations": list(item.record.limitations),
            }
            for item in report.results
            if item.record is not None
        ],
        "issues": [
            {
                "source_id": item.source_id,
                "capture_id": item.capture_id,
                "slide": item.slide,
                "error": item.error,
            }
            for item in report.results
            if item.status == "failed"
        ],
    }
    if args.report:
        document = build_ocr_backlog_report(
            operation=args.command,
            query=query,
            report=report,
            adapter=adapter,
            runtime={
                "timeout_seconds": args.timeout,
                "max_input_bytes": args.max_input_mb * 1024**2,
                "max_output_bytes": args.max_output_kb * 1024,
                "min_free_bytes": args.min_free_mb * 1024**2,
            },
        )
        report_path = Path(args.report).resolve()
        data = (
            json.dumps(
                document.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        write_immutable_bytes(report_path, data)
        output["report_path"] = str(report_path)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if report.failed else 0


def _ocr_capture(args: argparse.Namespace) -> int:
    packets = FileSystemSourcePacketRepository(Path(args.archive_dir).resolve())
    packet = next(
        (item for item in packets.iter_packets() if item.capture_id == args.capture_id),
        None,
    )
    if packet is None:
        raise ValueError("capture not found")
    if not any(artifact.role == ArtifactRole.ORIGINAL_IMAGE for artifact in packet.artifacts):
        raise ValueError("capture has no original images")
    return _run_ocr(
        args,
        OcrBacklogQuery(
            capture_limit=1,
            slide_limit=args.limit,
            capture_id=packet.capture_id,
            latest_only=False,
        ),
    )


def _ocr_backlog(args: argparse.Namespace) -> int:
    return _run_ocr(
        args,
        OcrBacklogQuery(
            capture_limit=args.capture_limit,
            slide_limit=args.slide_limit,
            collection=args.collection,
            kind=SourceKind(args.kind) if args.kind else None,
            status=CaptureStatus(args.status) if args.status else None,
            latest_only=not args.all_captures,
        ),
    )


def _ocr_evaluate(args: argparse.Namespace) -> int:
    evaluation_set = OcrEvaluationSet.model_validate_json(Path(args.samples).read_bytes())
    batch_reports = tuple(
        OcrBacklogReportDocument.model_validate_json(Path(path).read_bytes())
        for path in args.batch_report
    )
    report = OcrEvaluator(
        extractions=FileSystemExtractionRepository(Path(args.derived_dir).resolve()),
        max_text_chars=args.max_text_chars,
    ).evaluate(evaluation_set, batch_reports=batch_reports)
    if args.output:
        output_path = Path(args.output).resolve()
        data = (
            json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        write_immutable_bytes(output_path, data)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    unresolved = report.summary.failed + report.summary.missing + report.summary.oversized
    return 1 if unresolved else 0


def _project_obsidian(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    vault_root = Path(args.vault).resolve()
    generated_root = vault_root / "_generated" / "evidence-vault"
    packets = FileSystemSourcePacketRepository(archive_root)
    store = FileSystemProjectionStore(generated_root)
    extractions = FileSystemExtractionRepository(Path(args.derived_dir).resolve())
    projection = SourceCardProjection(
        packets=packets,
        artifacts=FileSystemArtifactStore(archive_root),
        store=store,
        extractions=extractions,
    )
    documents = projection.rebuild()
    overview_documents = ObsidianOverviewProjection(
        packets=packets,
        store=store,
        extractions=extractions,
    ).rebuild()
    generated_prefix = generated_root.relative_to(vault_root).as_posix()
    collection_documents = CollectionIndexProjection(
        packets=packets,
        store=store,
        generated_prefix=generated_prefix,
    ).rebuild()
    dashboard_documents = TriageDashboardProjection(store=store).rebuild()
    ideas_created = 0
    ideas_skipped = 0
    if args.scaffold_ideas:
        scaffold = IdeaScaffolder(
            packets=packets,
            notes=FileSystemHumanNoteStore(vault_root / "ideas"),
        ).scaffold()
        ideas_created = scaffold.created
        ideas_skipped = scaffold.skipped
    output = {
        "documents": documents,
        "overview_documents": overview_documents,
        "collection_documents": collection_documents,
        "dashboard_documents": dashboard_documents,
        "ideas_created": ideas_created,
        "ideas_skipped": ideas_skipped,
        "generated_root": str(generated_root / SourceCardProjection.NAMESPACE),
        "overview_root": str(generated_root / ObsidianOverviewProjection.NAMESPACE),
        "collections_root": str(generated_root / CollectionIndexProjection.NAMESPACE),
        "dashboards_root": str(generated_root / TriageDashboardProjection.NAMESPACE),
        "ideas_root": str(vault_root / "ideas"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _scaffold_ideas(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    vault_root = Path(args.vault).resolve()
    result = IdeaScaffolder(
        packets=FileSystemSourcePacketRepository(archive_root),
        notes=FileSystemHumanNoteStore(vault_root / "ideas"),
    ).scaffold()
    output = {
        "created": result.created,
        "skipped": result.skipped,
        "created_paths": list(result.created_paths),
        "ideas_root": str(vault_root / "ideas"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _llm_enrich(args: argparse.Namespace) -> int:
    config = resolve_llm_config(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )
    archive_root = Path(args.archive_dir).resolve()
    derived_root = Path(args.derived_dir).resolve()
    vault_root = Path(args.vault).resolve()
    generated_root = vault_root / "_generated" / "evidence-vault"
    report = LlmEnrichRunner(
        packets=FileSystemSourcePacketRepository(archive_root),
        client=OpenAIChatClient(
            OpenAIChatConfig(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )
        ),
        cache=FileSystemLlmCache(derived_root),
        model=config.model,
        generated_prefix=generated_root.relative_to(vault_root).as_posix(),
    ).run(
        LlmEnrichQuery(
            collection=args.collection,
            limit=args.limit,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    )
    note_path: str | None = None
    if report.note is not None:
        FileSystemProjectionStore(generated_root).replace_namespace(
            LlmEnrichRunner.NAMESPACE,
            [report.note],
        )
        note_path = str(generated_root / LlmEnrichRunner.NAMESPACE / report.note.path)
    output = {
        "collection": report.collection,
        "model": report.model,
        "processed": report.processed,
        "skipped_cached": report.skipped_cached,
        "failed": report.failed,
        "dry_run": report.dry_run,
        "note_path": note_path,
        "results": [
            {
                "source_id": item.source_id,
                "capture_id": item.capture_id,
                "status": item.status,
                "error": item.error,
            }
            for item in report.results
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if report.failed else 0


def _llm_catalog(args: argparse.Namespace) -> int:
    config = resolve_llm_config(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )
    archive_root = Path(args.archive_dir).resolve()
    derived_root = Path(args.derived_dir).resolve()
    vault_root = Path(args.vault).resolve()
    generated_root = vault_root / "_generated" / "evidence-vault"
    report = LlmCatalogRunner(
        packets=FileSystemSourcePacketRepository(archive_root),
        client=OpenAIChatClient(
            OpenAIChatConfig(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )
        ),
        cache=FileSystemLlmCache(derived_root, namespace=LlmCatalogRunner.NAMESPACE),
        model=config.model,
        generated_prefix=generated_root.relative_to(vault_root).as_posix(),
    ).run(
        LlmCatalogQuery(
            collection=args.collection,
            limit=args.limit,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    )
    note_path: str | None = None
    if report.note is not None:
        FileSystemProjectionStore(generated_root).replace_namespace(
            LlmCatalogRunner.NAMESPACE,
            [report.note],
        )
        note_path = str(generated_root / LlmCatalogRunner.NAMESPACE / report.note.path)
    output = {
        "collection": report.collection,
        "model": report.model,
        "processed": report.processed,
        "skipped_cached": report.skipped_cached,
        "unresolved": report.unresolved,
        "failed": report.failed,
        "catalog_size": report.catalog_size,
        "dry_run": report.dry_run,
        "note_path": note_path,
        "results": [
            {
                "source_id": item.source_id,
                "capture_id": item.capture_id,
                "status": item.status,
                "error": item.error,
            }
            for item in report.results
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if report.failed else 0


def _repo_catalog(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    derived_root = Path(args.derived_dir).resolve()
    vault_root = Path(args.vault).resolve()
    generated_root = vault_root / "_generated" / "evidence-vault"
    token = args.token or os.environ.get("GITHUB_TOKEN")
    report = RepoCatalogRunner(
        packets=FileSystemSourcePacketRepository(archive_root),
        fetcher=GitHubRepoClient(GitHubRepoConfig(token=token)),
        cache=FileSystemLlmCache(derived_root, namespace=RepoCatalogRunner.NAMESPACE),
        generated_prefix=generated_root.relative_to(vault_root).as_posix(),
    ).run(
        RepoCatalogQuery(
            collection=args.collection,
            limit=args.limit,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    )
    note_path: str | None = None
    if report.note is not None:
        FileSystemProjectionStore(generated_root).replace_namespace(
            RepoCatalogRunner.NAMESPACE,
            [report.note],
        )
        note_path = str(generated_root / RepoCatalogRunner.NAMESPACE / report.note.path)
    output = {
        "collection": report.collection,
        "repos_found": report.repos_found,
        "fetched": report.fetched,
        "skipped_cached": report.skipped_cached,
        "failed": report.failed,
        "skipped_non_github": report.skipped_non_github,
        "dry_run": report.dry_run,
        "note_path": note_path,
        "results": [
            {
                "repository": item.repository,
                "url": item.url,
                "status": item.status,
                "error": item.error,
            }
            for item in report.results
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if report.failed else 0


def _project_fit(args: argparse.Namespace) -> int:
    projects = _load_projects(Path(args.projects))
    config = resolve_llm_config(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )
    archive_root = Path(args.archive_dir).resolve()
    derived_root = Path(args.derived_dir).resolve()
    vault_root = Path(args.vault).resolve()
    generated_root = vault_root / "_generated" / "evidence-vault"
    report = ProjectFitRunner(
        packets=FileSystemSourcePacketRepository(archive_root),
        client=OpenAIChatClient(
            OpenAIChatConfig(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )
        ),
        cache=FileSystemLlmCache(derived_root, namespace=ProjectFitRunner.CACHE_NAMESPACE),
        model=config.model,
        projects=projects,
        extractions=FileSystemExtractionRepository(derived_root),
        generated_prefix=generated_root.relative_to(vault_root).as_posix(),
    ).run(
        ProjectFitQuery(
            collections=tuple(args.collection),
            limit=args.limit,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    )
    note_paths: list[str] = []
    if report.notes:
        FileSystemProjectionStore(generated_root).replace_namespace(
            ProjectFitRunner.NAMESPACE,
            report.notes,
        )
        note_paths = [
            str(generated_root / ProjectFitRunner.NAMESPACE / note.path) for note in report.notes
        ]
    output = {
        "collections": list(report.collections),
        "model": report.model,
        "items_scanned": report.items_scanned,
        "processed": report.processed,
        "skipped_cached": report.skipped_cached,
        "failed": report.failed,
        "unmatched": report.unmatched,
        "per_project": dict(report.per_project),
        "dry_run": report.dry_run,
        "note_paths": note_paths,
        "results": [
            {
                "source_id": item.source_id,
                "capture_id": item.capture_id,
                "status": item.status,
                "error": item.error,
            }
            for item in report.results
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if report.failed else 0


def _evidence_pack(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    artifacts = FileSystemArtifactStore(archive_root)
    builder = EvidencePackBuilder(
        packets=FileSystemSourcePacketRepository(archive_root),
        artifacts=artifacts,
        artifact_locator=artifacts,
        extractions=FileSystemExtractionRepository(Path(args.derived_dir).resolve()),
        max_text_artifact_bytes=args.max_text_kb * 1024,
    )
    pack = builder.build(
        args.query,
        limit=args.limit,
        collection=args.collection,
        kind=SourceKind(args.kind) if args.kind else None,
        status=CaptureStatus(args.status) if args.status else None,
        excerpt_limit=args.excerpt_limit,
    )
    if args.pack_format == "markdown":
        print(render_evidence_pack_markdown(pack, artifact_locator=artifacts))
    else:
        print(
            json.dumps(
                evidence_pack_to_dict(pack, artifact_locator=artifacts),
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _evidence_pack_verify(args: argparse.Namespace) -> int:
    pack = EvidencePackDocument.model_validate_json(Path(args.pack).read_bytes())
    report = EvidencePackVerifier(
        packets=FileSystemSourcePacketRepository(Path(args.archive_dir).resolve()),
        artifacts=FileSystemArtifactStore(Path(args.archive_dir).resolve()),
        extractions=FileSystemExtractionRepository(Path(args.derived_dir).resolve()),
    ).verify(pack, verify_bytes=args.verify_bytes)
    if args.output:
        output_path = Path(args.output).resolve()
        data = (
            json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        write_immutable_bytes(output_path, data)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def _search(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    index = SearchIndexBuilder(
        packets=FileSystemSourcePacketRepository(archive_root),
        artifacts=FileSystemArtifactStore(archive_root),
        extractions=FileSystemExtractionRepository(Path(args.derived_dir).resolve()),
        max_text_artifact_bytes=args.max_text_kb * 1024,
    ).build()
    results = index.search(
        args.query,
        limit=args.limit,
        collection=args.collection,
        kind=SourceKind(args.kind) if args.kind else None,
        status=CaptureStatus(args.status) if args.status else None,
    )
    output = {
        "query": args.query,
        "count": len(results),
        "results": [
            {
                "source_id": result.source_id,
                "capture_id": result.capture_id,
                "external_id": result.external_id,
                "title": result.title,
                "canonical_url": result.canonical_url,
                "kind": result.kind.value,
                "status": result.status.value,
                "collections": list(result.collections),
                "captured_at": result.captured_at.isoformat(),
                "relevance_score": result.score,
                "snippet": result.snippet,
            }
            for result in results
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _search_evaluate(args: argparse.Namespace) -> int:
    archive_root = Path(args.archive_dir).resolve()
    evaluation_set = SearchEvaluationSet.model_validate_json(Path(args.queries).read_bytes())
    index = SearchIndexBuilder(
        packets=FileSystemSourcePacketRepository(archive_root),
        artifacts=FileSystemArtifactStore(archive_root),
        extractions=FileSystemExtractionRepository(Path(args.derived_dir).resolve()),
        max_text_artifact_bytes=args.max_text_kb * 1024,
    ).build()
    report = SearchEvaluator(index).evaluate(evaluation_set)
    output = {
        "schema_version": "search-evaluation-report/v1",
        "query_count": report.query_count,
        "hit_rate": report.hit_rate,
        "mean_reciprocal_rank": report.mean_reciprocal_rank,
        "mean_recall": report.mean_recall,
        "mean_precision_at_k": report.mean_precision_at_k,
        "queries": [
            {
                "query": evaluation.specification.query,
                "k": evaluation.specification.k,
                "collection": evaluation.specification.collection,
                "kind": evaluation.specification.kind,
                "status": evaluation.specification.status,
                "notes": evaluation.specification.notes,
                "relevant_source_ids": list(evaluation.specification.relevant_source_ids),
                "relevant_capture_ids": list(evaluation.specification.relevant_capture_ids),
                "relevant_external_ids": list(evaluation.specification.relevant_external_ids),
                "retrieved_count": evaluation.retrieved_count,
                "relevant_retrieved": evaluation.relevant_retrieved,
                "found_labels": evaluation.found_labels,
                "first_relevant_rank": evaluation.first_relevant_rank,
                "hit": evaluation.hit,
                "recall": evaluation.recall,
                "precision_at_k": evaluation.precision_at_k,
                "reciprocal_rank": evaluation.reciprocal_rank,
                "results": [
                    {
                        "rank": result.rank,
                        "relevant": result.relevant,
                        "source_id": result.result.source_id,
                        "capture_id": result.result.capture_id,
                        "external_id": result.result.external_id,
                        "title": result.result.title,
                        "relevance_score": result.result.score,
                        "snippet": result.result.snippet,
                    }
                    for result in evaluation.results
                ],
            }
            for evaluation in report.queries
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _export_schema(args: argparse.Namespace) -> int:
    models: dict[str, type[BaseModel]] = {
        "source-packet": SourcePacket,
        "evidence-item": EvidenceItem,
        "capture-task": CaptureTask,
        "search-evaluation": SearchEvaluationSet,
        "extraction-record": ExtractionRecord,
        "evidence-pack": EvidencePackDocument,
        "evidence-pack-integrity": EvidencePackIntegrityReport,
        "ocr-backlog-report": OcrBacklogReportDocument,
        "ocr-evaluation": OcrEvaluationSet,
        "ocr-evaluation-report": OcrEvaluationReport,
        "collection-capture": CollectionCaptureDocument,
    }
    schema = models[args.contract].model_json_schema()
    print(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _add_tiktok_export_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("export", help="path to TikTok JSON or TXT export")
    command.add_argument("--format", dest="file_format", choices=("json", "txt"))
    command.add_argument(
        "--export-timezone",
        help="timezone for naive export dates, for example Europe/Moscow",
    )
    command.add_argument("--limit", type=int, help="process at most N unique tasks")


def _add_ocr_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--archive-dir", default="data/archive")
    command.add_argument("--derived-dir", default="data/derived")
    command.add_argument("--executable", default="tesseract")
    command.add_argument("--temp-dir", default="data/tmp/tesseract")
    command.add_argument("--tessdata-dir")
    command.add_argument("--language", default="eng+rus")
    command.add_argument("--psm", type=int, default=3)
    command.add_argument("--timeout", type=float, default=60)
    command.add_argument("--max-input-mb", type=int, default=50)
    command.add_argument("--max-output-kb", type=int, default=1024)
    command.add_argument("--min-free-mb", type=int, default=64)
    command.add_argument("--concurrency", type=int, default=2)
    command.add_argument(
        "--report",
        help="write an immutable ocr-backlog-report/v1 JSON; use a new path per run",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evidence-vault")
    subcommands = parser.add_subparsers(dest="command", required=True)

    archive = subcommands.add_parser(
        "archive-capture",
        help="archive one local capture manifest",
    )
    archive.add_argument("manifest", help="path to capture-manifest/v1 JSON")
    archive.add_argument(
        "--archive-dir",
        default="data/archive",
        help="immutable archive root (default: data/archive)",
    )
    archive.set_defaults(handler=_archive_capture)

    audit = subcommands.add_parser(
        "archive-audit",
        help="verify artifact references and optionally hash all archived bytes",
    )
    audit.add_argument("--archive-dir", default="data/archive")
    audit.add_argument("--verify-bytes", action="store_true")
    audit.add_argument("--max-issues", type=int, default=100)
    audit.set_defaults(handler=_archive_audit)

    capture_report = subcommands.add_parser(
        "capture-report",
        help="report capture status, retries, durations, and recent failures",
    )
    capture_report.add_argument("--control-db", default="data/control/capture.sqlite3")
    capture_report.add_argument(
        "--format", dest="report_format", choices=("json", "markdown"), default="json"
    )
    capture_report.add_argument("--stale-after-seconds", type=float, default=3600)
    capture_report.add_argument("--recent-errors", type=int, default=20)
    capture_report.set_defaults(handler=_capture_report)

    inspect = subcommands.add_parser(
        "tiktok-inspect",
        help="stream and inspect a TikTok favorites export without writing data",
    )
    _add_tiktok_export_arguments(inspect)
    inspect.set_defaults(handler=_tiktok_inspect)

    tiktok_import = subcommands.add_parser(
        "tiktok-import",
        help="archive TikTok saved-item metadata with resumable bounded workers",
    )
    _add_tiktok_export_arguments(tiktok_import)
    tiktok_import.add_argument("--archive-dir", default="data/archive")
    tiktok_import.add_argument("--control-db", default="data/control/capture.sqlite3")
    tiktok_import.add_argument("--concurrency", type=int, default=4)
    tiktok_import.add_argument("--max-attempts", type=int, default=3)
    tiktok_import.set_defaults(handler=_tiktok_import)

    download = subcommands.add_parser(
        "tiktok-download",
        help="capture TikTok videos through yt-dlp with bounded resumable workers",
    )
    _add_tiktok_export_arguments(download)
    download.add_argument("--archive-dir", default="data/archive")
    download.add_argument("--control-db", default="data/control/capture.sqlite3")
    download.add_argument("--concurrency", type=int, default=4)
    download.add_argument("--max-attempts", type=int, default=3)
    download.add_argument("--executable", default="yt-dlp")
    download.add_argument("--temp-dir", default="data/tmp/yt-dlp")
    download.add_argument("--format-selector", default="best")
    download.add_argument("--site-limit", type=int, default=2)
    download.add_argument("--timeout", type=float, default=300)
    download.add_argument("--socket-timeout", type=int, default=30)
    download.add_argument("--max-file-mb", type=int, default=2048)
    download.add_argument("--min-free-mb", type=int, default=512)
    download.set_defaults(handler=_tiktok_download)

    slides = subcommands.add_parser(
        "tiktok-slides",
        help="resolve only TikTok slideshows through gallery-dl with inventory checks",
    )
    _add_tiktok_export_arguments(slides)
    slides.add_argument("--archive-dir", default="data/archive")
    slides.add_argument("--control-db", default="data/control/capture.sqlite3")
    slides.add_argument("--concurrency", type=int, default=4)
    slides.add_argument("--max-attempts", type=int, default=3)
    slides.add_argument("--executable", default="gallery-dl")
    slides.add_argument("--temp-dir", default="data/tmp/gallery-dl")
    slides.add_argument("--site-limit", type=int, default=2)
    slides.add_argument("--timeout", type=float, default=300)
    slides.add_argument("--socket-timeout", type=float, default=30)
    slides.add_argument("--max-slides", type=int, default=50)
    slides.add_argument("--max-slide-mb", type=int, default=50)
    slides.add_argument("--max-total-mb", type=int, default=500)
    slides.add_argument("--min-free-mb", type=int, default=512)
    slides.set_defaults(handler=_tiktok_slides)

    import_collections = subcommands.add_parser(
        "import-collections",
        help="archive collection-capture/v1 metadata with resumable bounded workers",
    )
    import_collections.add_argument("file", help="path to collection-capture/v1 JSON")
    import_collections.add_argument("--archive-dir", default="data/archive")
    import_collections.add_argument("--control-db", default="data/control/capture.sqlite3")
    import_collections.add_argument("--concurrency", type=int, default=4)
    import_collections.add_argument("--limit", type=int, help="process at most N unique tasks")
    import_collections.set_defaults(handler=_import_collections)

    fetch_slides = subcommands.add_parser(
        "fetch-slides",
        help="download slideshow images for a collection and archive them as image artifacts",
    )
    fetch_slides.add_argument("--collection", required=True)
    fetch_slides.add_argument("--archive-dir", default="data/archive")
    fetch_slides.add_argument("--control-db", default="data/control/capture.sqlite3")
    fetch_slides.add_argument("--limit", type=int, help="process at most N sources")
    fetch_slides.add_argument(
        "--max-slides",
        type=int,
        default=50,
        help="download at most N images in total across the run",
    )
    fetch_slides.add_argument(
        "--max-mb",
        type=int,
        default=20,
        help="per-image byte limit in MiB",
    )
    fetch_slides.set_defaults(handler=_fetch_slides)

    ocr = subcommands.add_parser(
        "ocr-capture",
        help="extract searchable text from original images in one capture",
    )
    ocr.add_argument("capture_id")
    _add_ocr_arguments(ocr)
    ocr.add_argument("--limit", type=int, default=50)
    ocr.set_defaults(handler=_ocr_capture)

    backlog = subcommands.add_parser(
        "ocr-backlog",
        help="run bounded resumable OCR over filtered archived captures",
    )
    _add_ocr_arguments(backlog)
    backlog.add_argument("--capture-limit", type=int, default=10)
    backlog.add_argument("--slide-limit", type=int, default=100)
    backlog.add_argument("--collection")
    backlog.add_argument("--kind", choices=tuple(item.value for item in SourceKind))
    backlog.add_argument("--status", choices=tuple(item.value for item in CaptureStatus))
    backlog.add_argument(
        "--all-captures",
        action="store_true",
        help="include superseded captures instead of latest per source",
    )
    backlog.set_defaults(handler=_ocr_backlog)

    evaluate_ocr = subcommands.add_parser(
        "ocr-evaluate",
        help="score golden OCR samples and classify failed or missing records",
    )
    evaluate_ocr.add_argument("samples", help="path to ocr-evaluation/v1 JSON")
    evaluate_ocr.add_argument("--derived-dir", default="data/derived")
    evaluate_ocr.add_argument(
        "--batch-report",
        action="append",
        default=[],
        help="saved ocr-backlog-report/v1 JSON; repeat for resume history",
    )
    evaluate_ocr.add_argument("--max-text-chars", type=int, default=20_000)
    evaluate_ocr.add_argument("--output", help="write an immutable evaluation JSON report")
    evaluate_ocr.set_defaults(handler=_ocr_evaluate)

    project = subcommands.add_parser(
        "project-obsidian",
        help="rebuild generated Obsidian source cards from canonical packets",
    )
    project.add_argument("--archive-dir", default="data/archive")
    project.add_argument("--derived-dir", default="data/derived")
    project.add_argument("--vault", default="data/vault")
    project.add_argument(
        "--scaffold-ideas",
        action="store_true",
        help="also create missing human idea notes under <vault>/ideas",
    )
    project.set_defaults(handler=_project_obsidian)

    scaffold_ideas = subcommands.add_parser(
        "scaffold-ideas",
        help="create missing human idea notes under <vault>/ideas without overwriting",
    )
    scaffold_ideas.add_argument("--archive-dir", default="data/archive")
    scaffold_ideas.add_argument("--vault", default="data/vault")
    scaffold_ideas.set_defaults(handler=_scaffold_ideas)

    llm_enrich = subcommands.add_parser(
        "llm-enrich",
        help="enrich a collection's latest captures with an OpenAI-compatible chat model",
    )
    llm_enrich.add_argument("--collection", required=True)
    llm_enrich.add_argument("--archive-dir", default="data/archive")
    llm_enrich.add_argument("--derived-dir", default="data/derived")
    llm_enrich.add_argument("--vault", default="data/vault")
    llm_enrich.add_argument("--limit", type=int, help="process at most N captures")
    llm_enrich.add_argument("--refresh", action="store_true", help="ignore the cache and re-query")
    llm_enrich.add_argument("--dry-run", action="store_true", help="report without writing")
    llm_enrich.add_argument("--base-url")
    llm_enrich.add_argument("--api-key")
    llm_enrich.add_argument("--model")
    llm_enrich.set_defaults(handler=_llm_enrich)

    llm_catalog = subcommands.add_parser(
        "llm-catalog",
        help="build a deduplicated catalog of works from a collection's latest captures",
    )
    llm_catalog.add_argument("--collection", required=True)
    llm_catalog.add_argument("--archive-dir", default="data/archive")
    llm_catalog.add_argument("--derived-dir", default="data/derived")
    llm_catalog.add_argument("--vault", default="data/vault")
    llm_catalog.add_argument("--limit", type=int, help="process at most N captures")
    llm_catalog.add_argument("--refresh", action="store_true", help="ignore the cache and re-query")
    llm_catalog.add_argument("--dry-run", action="store_true", help="report without writing")
    llm_catalog.add_argument("--base-url")
    llm_catalog.add_argument("--api-key")
    llm_catalog.add_argument("--model")
    llm_catalog.set_defaults(handler=_llm_catalog)

    repo_catalog = subcommands.add_parser(
        "repo-catalog",
        help="build a live GitHub repository catalog for one collection",
    )
    repo_catalog.add_argument("--collection", required=True)
    repo_catalog.add_argument("--archive-dir", default="data/archive")
    repo_catalog.add_argument("--derived-dir", default="data/derived")
    repo_catalog.add_argument("--vault", default="data/vault")
    repo_catalog.add_argument("--limit", type=int, help="process at most N captures")
    repo_catalog.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the cache and re-fetch repository metadata",
    )
    repo_catalog.add_argument("--dry-run", action="store_true", help="report without writing")
    repo_catalog.add_argument("--token", help="GitHub API token; defaults to env GITHUB_TOKEN")
    repo_catalog.set_defaults(handler=_repo_catalog)

    project_fit = subcommands.add_parser(
        "project-fit",
        help="match a collection's items to the user's projects and write per-project briefs",
    )
    project_fit.add_argument(
        "--collection",
        action="append",
        required=True,
        help="collection name; repeat to scan several collections",
    )
    project_fit.add_argument("--projects", default="data/projects.json")
    project_fit.add_argument("--archive-dir", default="data/archive")
    project_fit.add_argument("--derived-dir", default="data/derived")
    project_fit.add_argument("--vault", default="data/vault")
    project_fit.add_argument("--limit", type=int, help="process at most N captures")
    project_fit.add_argument("--refresh", action="store_true", help="ignore the cache and re-query")
    project_fit.add_argument("--dry-run", action="store_true", help="report without writing")
    project_fit.add_argument("--base-url")
    project_fit.add_argument("--api-key")
    project_fit.add_argument("--model")
    project_fit.set_defaults(handler=_project_fit)

    evidence_pack = subcommands.add_parser(
        "evidence-pack",
        help="build a retrieval-only JSON or Markdown evidence pack",
    )
    evidence_pack.add_argument("query")
    evidence_pack.add_argument("--archive-dir", default="data/archive")
    evidence_pack.add_argument("--derived-dir", default="data/derived")
    evidence_pack.add_argument(
        "--format",
        dest="pack_format",
        choices=("json", "markdown"),
        default="json",
    )
    evidence_pack.add_argument("--limit", type=int, default=10)
    evidence_pack.add_argument("--excerpt-limit", type=int, default=3)
    evidence_pack.add_argument("--collection")
    evidence_pack.add_argument("--kind", choices=tuple(item.value for item in SourceKind))
    evidence_pack.add_argument("--status", choices=tuple(item.value for item in CaptureStatus))
    evidence_pack.add_argument("--max-text-kb", type=int, default=1024)
    evidence_pack.set_defaults(handler=_evidence_pack)

    verify_pack = subcommands.add_parser(
        "evidence-pack-verify",
        help="verify a saved evidence pack against packets and derived records",
    )
    verify_pack.add_argument("pack", help="path to evidence-pack/v1 JSON")
    verify_pack.add_argument("--archive-dir", default="data/archive")
    verify_pack.add_argument("--derived-dir", default="data/derived")
    verify_pack.add_argument("--verify-bytes", action="store_true")
    verify_pack.add_argument("--output", help="write an immutable integrity report")
    verify_pack.set_defaults(handler=_evidence_pack_verify)

    search = subcommands.add_parser("search", help="search archived source metadata and text")
    search.add_argument("query")
    search.add_argument("--archive-dir", default="data/archive")
    search.add_argument("--derived-dir", default="data/derived")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--collection")
    search.add_argument("--kind", choices=tuple(item.value for item in SourceKind))
    search.add_argument("--status", choices=tuple(item.value for item in CaptureStatus))
    search.add_argument("--max-text-kb", type=int, default=1024)
    search.set_defaults(handler=_search)

    evaluate = subcommands.add_parser(
        "search-evaluate",
        help="evaluate judged queries with hit rate, MRR, recall, and precision",
    )
    evaluate.add_argument("queries", help="path to search-evaluation/v1 JSON")
    evaluate.add_argument("--archive-dir", default="data/archive")
    evaluate.add_argument("--derived-dir", default="data/derived")
    evaluate.add_argument("--max-text-kb", type=int, default=1024)
    evaluate.set_defaults(handler=_search_evaluate)

    schema = subcommands.add_parser("schema", help="export a versioned JSON Schema")
    schema.add_argument(
        "contract",
        choices=(
            "source-packet",
            "evidence-item",
            "capture-task",
            "search-evaluation",
            "extraction-record",
            "evidence-pack",
            "evidence-pack-integrity",
            "ocr-backlog-report",
            "ocr-evaluation",
            "ocr-evaluation-report",
            "collection-capture",
        ),
    )
    schema.set_defaults(handler=_export_schema)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, ValidationError, ImmutableStoreConflict) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 2
