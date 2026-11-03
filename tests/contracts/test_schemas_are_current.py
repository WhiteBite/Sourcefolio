from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from evidence_vault.application.evidence_pack import EvidencePackDocument
from evidence_vault.application.evidence_pack_integrity import EvidencePackIntegrityReport
from evidence_vault.application.ocr_backlog_report import OcrBacklogReportDocument
from evidence_vault.application.ocr_evaluation import OcrEvaluationReport, OcrEvaluationSet
from evidence_vault.application.search_evaluation import SearchEvaluationSet
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.evidence import EvidenceItem
from evidence_vault.domain.extraction import ExtractionRecord
from evidence_vault.domain.source import SourcePacket

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
    ],
)
def test_exported_schema_matches_domain_contract(
    filename: str,
    model: type[BaseModel],
) -> None:
    exported = json.loads((CONTRACTS / filename).read_text(encoding="utf-8"))
    assert exported == model.model_json_schema()
