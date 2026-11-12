from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from sourcefolio.adapters.tiktok.collections import CollectionCaptureDocument
from sourcefolio.application.evidence_pack import EvidencePackDocument
from sourcefolio.application.evidence_pack_integrity import EvidencePackIntegrityReport
from sourcefolio.application.ocr_backlog_report import OcrBacklogReportDocument
from sourcefolio.application.ocr_evaluation import OcrEvaluationReport, OcrEvaluationSet
from sourcefolio.application.search_evaluation import SearchEvaluationSet
from sourcefolio.domain.capture import CaptureTask
from sourcefolio.domain.evidence import EvidenceItem
from sourcefolio.domain.extraction import ExtractionRecord
from sourcefolio.domain.registry import (
    Candidate,
    CandidateDecision,
    RegistryRoute,
    RegistrySnapshot,
)
from sourcefolio.domain.source import SourcePacket

CONTRACTS = Path(__file__).parents[2] / "contracts"


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("source-packet-v1.schema.json", SourcePacket),
        ("evidence-item-v1.schema.json", EvidenceItem),
        ("capture-task-v1.schema.json", CaptureTask),
        ("search-evaluation-v1.schema.json", SearchEvaluationSet),
        ("extraction-record-v1.schema.json", ExtractionRecord),
        ("evidence-pack-v1.schema.json", EvidencePackDocument),
        ("evidence-pack-integrity-v1.schema.json", EvidencePackIntegrityReport),
        ("ocr-backlog-report-v1.schema.json", OcrBacklogReportDocument),
        ("ocr-evaluation-v1.schema.json", OcrEvaluationSet),
        ("ocr-evaluation-report-v1.schema.json", OcrEvaluationReport),
        ("collection-capture-v1.schema.json", CollectionCaptureDocument),
        ("registry-route-v1.schema.json", RegistryRoute),
        ("candidate-record-v1.schema.json", Candidate),
        ("registry-snapshot-v1.schema.json", RegistrySnapshot),
        ("candidate-decision-v1.schema.json", CandidateDecision),
    ],
)
def test_exported_schema_matches_domain_contract(
    filename: str,
    model: type[BaseModel],
) -> None:
    exported = json.loads((CONTRACTS / filename).read_text(encoding="utf-8"))
    assert exported == model.model_json_schema()
