from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from evidence_vault.application.search import LexicalSearchIndex, SearchResult
from evidence_vault.domain.common import ContractModel, NonEmptyStr
from evidence_vault.domain.source import (
    CaptureId,
    CaptureStatus,
    SourceId,
    SourceKind,
)


class SearchEvaluationQuery(ContractModel):
    """One judged query; labels are assumed exhaustive for the evaluated corpus."""

    query: NonEmptyStr
    k: int = Field(default=10, ge=1, le=100)
    collection: NonEmptyStr | None = None
    kind: SourceKind | None = None
    status: CaptureStatus | None = None
    relevant_source_ids: tuple[SourceId, ...] = ()
    relevant_capture_ids: tuple[CaptureId, ...] = ()
    relevant_external_ids: tuple[NonEmptyStr, ...] = ()
    notes: str | None = None

    @model_validator(mode="after")
    def one_label_namespace(self) -> SearchEvaluationQuery:
        namespaces = (
            bool(self.relevant_source_ids),
            bool(self.relevant_capture_ids),
            bool(self.relevant_external_ids),
        )
        if sum(namespaces) != 1:
            raise ValueError("query requires exactly one relevant-ID namespace")
        return self


class SearchEvaluationSet(ContractModel):
    schema_version: Literal["search-evaluation/v1"]
    queries: tuple[SearchEvaluationQuery, ...] = Field(min_length=1)


@dataclass(frozen=True)
class EvaluatedResult:
    rank: int
    result: SearchResult
    relevant: bool


@dataclass(frozen=True)
class QueryEvaluation:
    specification: SearchEvaluationQuery
    retrieved_count: int
    relevant_retrieved: int
    found_labels: int
    first_relevant_rank: int | None
    recall: float
    precision_at_k: float
    reciprocal_rank: float
    results: tuple[EvaluatedResult, ...]

    @property
    def hit(self) -> bool:
        return self.first_relevant_rank is not None


@dataclass(frozen=True)
class SearchEvaluationReport:
    query_count: int
    hit_rate: float
    mean_reciprocal_rank: float
    mean_recall: float
    mean_precision_at_k: float
    queries: tuple[QueryEvaluation, ...]


class SearchEvaluator:
    def __init__(self, index: LexicalSearchIndex) -> None:
        self._index = index

    def evaluate(self, evaluation_set: SearchEvaluationSet) -> SearchEvaluationReport:
        evaluations = tuple(
            self._evaluate_query(specification) for specification in evaluation_set.queries
        )
        count = len(evaluations)
        return SearchEvaluationReport(
            query_count=count,
            hit_rate=sum(item.hit for item in evaluations) / count,
            mean_reciprocal_rank=sum(item.reciprocal_rank for item in evaluations) / count,
            mean_recall=sum(item.recall for item in evaluations) / count,
            mean_precision_at_k=sum(item.precision_at_k for item in evaluations) / count,
            queries=evaluations,
        )

    def _evaluate_query(self, specification: SearchEvaluationQuery) -> QueryEvaluation:
        results = self._index.search(
            specification.query,
            limit=specification.k,
            collection=specification.collection,
            kind=specification.kind,
            status=specification.status,
        )
        expected, mode = self._expected_labels(specification)
        found_labels: set[str] = set()
        evaluated: list[EvaluatedResult] = []
        first_relevant_rank: int | None = None
        relevant_retrieved = 0

        for rank, result in enumerate(results, start=1):
            candidate = {
                "source": result.source_id,
                "capture": result.capture_id,
                "external": result.external_id,
            }[mode]
            matched = expected & ({candidate} if candidate is not None else set())
            relevant = bool(matched)
            if relevant:
                relevant_retrieved += 1
                found_labels.update(matched)
                if first_relevant_rank is None:
                    first_relevant_rank = rank
            evaluated.append(EvaluatedResult(rank=rank, result=result, relevant=relevant))

        reciprocal_rank = 1.0 / first_relevant_rank if first_relevant_rank else 0.0
        return QueryEvaluation(
            specification=specification,
            retrieved_count=len(results),
            relevant_retrieved=relevant_retrieved,
            found_labels=len(found_labels),
            first_relevant_rank=first_relevant_rank,
            recall=len(found_labels) / len(expected),
            precision_at_k=relevant_retrieved / specification.k,
            reciprocal_rank=reciprocal_rank,
            results=tuple(evaluated),
        )

    @staticmethod
    def _expected_labels(
        specification: SearchEvaluationQuery,
    ) -> tuple[set[str], str]:
        if specification.relevant_source_ids:
            return set(specification.relevant_source_ids), "source"
        if specification.relevant_capture_ids:
            return set(specification.relevant_capture_ids), "capture"
        return set(specification.relevant_external_ids), "external"
