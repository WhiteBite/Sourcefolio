from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evidence_vault.application.search import LexicalSearchIndex, SearchDocument
from evidence_vault.application.search_evaluation import (
    SearchEvaluationSet,
    SearchEvaluator,
)
from evidence_vault.domain.source import CaptureStatus, SourceKind

SOURCE_A = "src_" + "1" * 64
SOURCE_B = "src_" + "2" * 64
CAPTURE_A = "cap_" + "a" * 64
CAPTURE_B = "cap_" + "b" * 64


def document(
    source_id: str,
    capture_id: str,
    external_id: str,
    title: str,
    content: str,
) -> SearchDocument:
    return SearchDocument(
        source_id=source_id,
        capture_id=capture_id,
        external_id=external_id,
        title=title,
        canonical_url=None,
        kind=SourceKind.VIDEO,
        status=CaptureStatus.COMPLETE,
        collections=("Идеи",),
        captured_at=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
        content=content,
    )


def test_evaluation_reports_ranking_metrics_and_per_query_results() -> None:
    index = LexicalSearchIndex(
        (
            document(SOURCE_A, CAPTURE_A, "123", "other", "нейронки agents"),
            document(SOURCE_B, CAPTURE_B, "456", "нейронки", "нейронки"),
        )
    )
    evaluation_set = SearchEvaluationSet.model_validate(
        {
            "schema_version": "search-evaluation/v1",
            "queries": [
                {
                    "query": "нейронки",
                    "k": 2,
                    "relevant_external_ids": ["123"],
                },
                {
                    "query": "agents",
                    "k": 2,
                    "relevant_capture_ids": [CAPTURE_A],
                },
            ],
        }
    )

    report = SearchEvaluator(index).evaluate(evaluation_set)

    assert report.query_count == 2
    assert report.hit_rate == 1.0
    assert report.mean_reciprocal_rank == 0.75
    assert report.mean_recall == 1.0
    assert report.mean_precision_at_k == 0.5
    first = report.queries[0]
    assert first.first_relevant_rank == 2
    assert first.results[0].result.external_id == "456"
    assert first.results[0].relevant is False
    assert first.results[1].relevant is True


def test_evaluation_rejects_ambiguous_or_missing_labels() -> None:
    with pytest.raises(ValidationError):
        SearchEvaluationSet.model_validate(
            {
                "schema_version": "search-evaluation/v1",
                "queries": [
                    {
                        "query": "test",
                        "relevant_external_ids": ["123"],
                        "relevant_capture_ids": [CAPTURE_A],
                    }
                ],
            }
        )
