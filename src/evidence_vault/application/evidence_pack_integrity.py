from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from evidence_vault.application.evidence_pack import (
    EvidencePackDocument,
    EvidencePackSourceDocument,
)
from evidence_vault.application.extraction_ports import ExtractionRepository
from evidence_vault.application.ports import ArtifactStore, SourcePacketRepository
from evidence_vault.domain.common import ContractModel, NonEmptyStr, UtcDateTime
from evidence_vault.domain.extraction import EvidenceLocator, ExtractionId
from evidence_vault.domain.source import ArtifactId, CaptureId, SourceId

_TERM = re.compile(r"\w+", re.UNICODE)


class EvidencePackIntegrityIssue(ContractModel):
    code: NonEmptyStr
    detail: NonEmptyStr
    source_id: SourceId | None = None
    capture_id: CaptureId | None = None
    artifact_id: ArtifactId | None = None
    extraction_id: ExtractionId | None = None


class EvidencePackIntegrityReport(ContractModel):
    schema_version: Literal["evidence-pack-integrity/v1"] = "evidence-pack-integrity/v1"
    checked_at: UtcDateTime
    query: str
    pack_generated_at: UtcDateTime
    verify_bytes: bool
    source_count: int = Field(ge=0)
    artifact_count: int = Field(ge=0)
    excerpt_count: int = Field(ge=0)
    bytes_checked: int = Field(ge=0)
    bytes_valid: int = Field(ge=0)
    ok: bool
    issue_count: int = Field(ge=0)
    issues: tuple[EvidencePackIntegrityIssue, ...] = ()
    limitations: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def counts_match(self) -> EvidencePackIntegrityReport:
        if self.issue_count != len(self.issues):
            raise ValueError("issue_count does not match issues")
        if self.ok != (self.issue_count == 0):
            raise ValueError("ok does not match issue_count")
        if self.bytes_valid > self.bytes_checked:
            raise ValueError("bytes_valid cannot exceed bytes_checked")
        return self


class EvidencePackVerifier:
    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactStore,
        extractions: ExtractionRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._artifacts = artifacts
        self._extractions = extractions
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify(
        self,
        pack: EvidencePackDocument,
        *,
        verify_bytes: bool = False,
    ) -> EvidencePackIntegrityReport:
        issues: list[EvidencePackIntegrityIssue] = []
        for source in pack.sources:
            self._verify_source(source, pack.query, issues)
        unique_artifacts = {
            artifact.artifact_id: artifact
            for source in pack.sources
            for artifact in source.artifacts
        }
        bytes_checked = 0
        bytes_valid = 0
        if verify_bytes:
            for artifact_id in sorted(unique_artifacts):
                bytes_checked += 1
                try:
                    valid = self._artifacts.verify(artifact_id)
                except OSError:
                    valid = False
                if valid:
                    bytes_valid += 1
                else:
                    issues.append(
                        EvidencePackIntegrityIssue(
                            code="artifact_hash_mismatch",
                            detail=(
                                "artifact bytes are missing or do not match their content address"
                            ),
                            artifact_id=artifact_id,
                        )
                    )
        limitations = [
            "Integrity checks do not establish factual correctness or source trust.",
        ]
        if not verify_bytes:
            limitations.append(
                "Artifact bytes were not hashed; enable verify_bytes for content integrity."
            )
        return EvidencePackIntegrityReport(
            checked_at=self._clock(),
            query=pack.query,
            pack_generated_at=pack.generated_at,
            verify_bytes=verify_bytes,
            source_count=len(pack.sources),
            artifact_count=sum(len(source.artifacts) for source in pack.sources),
            excerpt_count=sum(len(source.excerpts) for source in pack.sources),
            bytes_checked=bytes_checked,
            bytes_valid=bytes_valid,
            ok=not issues,
            issue_count=len(issues),
            issues=tuple(issues),
            limitations=tuple(limitations),
        )

    def _verify_source(
        self,
        source: EvidencePackSourceDocument,
        query: str,
        issues: list[EvidencePackIntegrityIssue],
    ) -> None:
        try:
            packet = self._packets.get(source.source_id, source.capture_id)
        except (OSError, ValueError) as exc:
            issues.append(
                EvidencePackIntegrityIssue(
                    code="invalid_source_packet",
                    detail=f"source packet could not be read: {type(exc).__name__}",
                    source_id=source.source_id,
                    capture_id=source.capture_id,
                )
            )
            return
        if packet is None:
            issues.append(
                EvidencePackIntegrityIssue(
                    code="missing_source_packet",
                    detail="source packet referenced by the pack is missing",
                    source_id=source.source_id,
                    capture_id=source.capture_id,
                )
            )
            return
        expected_title = packet.title or packet.source.external_id or packet.source_id
        if (
            source.title != expected_title
            or source.external_id != packet.source.external_id
            or source.canonical_url != packet.source.canonical_url
            or source.kind != packet.source.kind
            or source.status != packet.status
            or source.captured_at != packet.captured_at
            or source.collections != tuple(item.name for item in packet.collections)
            or source.collector != packet.collector
        ):
            issues.append(
                EvidencePackIntegrityIssue(
                    code="source_metadata_mismatch",
                    detail="pack source metadata differs from the immutable source packet",
                    source_id=source.source_id,
                    capture_id=source.capture_id,
                )
            )

        actual_artifacts = {artifact.artifact_id: artifact for artifact in packet.artifacts}
        packed_artifacts = {artifact.artifact_id: artifact for artifact in source.artifacts}
        for artifact_id, packed in packed_artifacts.items():
            actual = actual_artifacts.get(artifact_id)
            if actual is None:
                issues.append(
                    EvidencePackIntegrityIssue(
                        code="dangling_artifact_ref",
                        detail="pack artifact is not present in the source packet",
                        source_id=source.source_id,
                        capture_id=source.capture_id,
                        artifact_id=artifact_id,
                    )
                )
            elif (
                packed.role != actual.role
                or packed.media_type != actual.media_type
                or packed.size_bytes != actual.size_bytes
                or packed.sha256 != actual.sha256
                or packed.original_filename != actual.original_filename
            ):
                issues.append(
                    EvidencePackIntegrityIssue(
                        code="artifact_metadata_mismatch",
                        detail="pack artifact metadata differs from the source packet",
                        source_id=source.source_id,
                        capture_id=source.capture_id,
                        artifact_id=artifact_id,
                    )
                )
        for artifact_id in actual_artifacts.keys() - packed_artifacts.keys():
            issues.append(
                EvidencePackIntegrityIssue(
                    code="packet_artifact_omitted",
                    detail="source packet artifact is omitted from the pack",
                    source_id=source.source_id,
                    capture_id=source.capture_id,
                    artifact_id=artifact_id,
                )
            )

        try:
            records = {
                record.extraction_id: record
                for record in self._extractions.iter_for_capture(source.capture_id)
            }
        except (OSError, ValueError) as exc:
            issues.append(
                EvidencePackIntegrityIssue(
                    code="invalid_extraction_store",
                    detail=f"derived records could not be read: {type(exc).__name__}",
                    source_id=source.source_id,
                    capture_id=source.capture_id,
                )
            )
            records = {}
        seen_excerpts: set[str] = set()
        for excerpt in source.excerpts:
            if excerpt.extraction_id in seen_excerpts:
                issues.append(
                    EvidencePackIntegrityIssue(
                        code="duplicate_excerpt",
                        detail="the same extraction is included more than once in a source",
                        source_id=source.source_id,
                        capture_id=source.capture_id,
                        extraction_id=excerpt.extraction_id,
                    )
                )
            seen_excerpts.add(excerpt.extraction_id)
            record = records.get(excerpt.extraction_id)
            if record is None:
                issues.append(
                    EvidencePackIntegrityIssue(
                        code="dangling_extraction_ref",
                        detail="pack excerpt refers to a missing extraction record",
                        source_id=source.source_id,
                        capture_id=source.capture_id,
                        extraction_id=excerpt.extraction_id,
                    )
                )
                continue
            request = record.request
            packed_locator = EvidenceLocator(
                kind=excerpt.locator.kind,
                slide=excerpt.locator.slide,
                start_seconds=excerpt.locator.start_seconds,
                end_seconds=excerpt.locator.end_seconds,
                start_char=excerpt.locator.start_char,
                end_char=excerpt.locator.end_char,
            )
            if (
                excerpt.request_id != record.request_id
                or excerpt.source_id != request.source_id
                or excerpt.capture_id != request.capture_id
                or excerpt.artifact_id != request.artifact_id
                or excerpt.kind != request.kind
                or packed_locator != request.locator
            ):
                issues.append(
                    EvidencePackIntegrityIssue(
                        code="extraction_request_mismatch",
                        detail="pack excerpt request fields differ from the extraction record",
                        source_id=source.source_id,
                        capture_id=source.capture_id,
                        extraction_id=excerpt.extraction_id,
                    )
                )
            if (
                excerpt.created_at != record.created_at
                or excerpt.excerpt != record.excerpt
                or excerpt.extractor != request.extractor
                or excerpt.config != request.config
                or excerpt.limitations != record.limitations
            ):
                issues.append(
                    EvidencePackIntegrityIssue(
                        code="excerpt_provenance_mismatch",
                        detail="pack excerpt text or provenance differs from the extraction record",
                        source_id=source.source_id,
                        capture_id=source.capture_id,
                        extraction_id=excerpt.extraction_id,
                    )
                )
            expected_match = _query_matches(record.text, query)
            if excerpt.matched_query != expected_match:
                issues.append(
                    EvidencePackIntegrityIssue(
                        code="query_match_mismatch",
                        detail="matched_query does not match the stored derived text",
                        source_id=source.source_id,
                        capture_id=source.capture_id,
                        extraction_id=excerpt.extraction_id,
                    )
                )


def _query_matches(text: str, query: str) -> bool:
    terms = [term.casefold() for term in _TERM.findall(query)]
    folded = text.casefold()
    return bool(terms) and all(term in folded for term in terms)
