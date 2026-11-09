from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class DurationSummary:
    count: int
    total_seconds: float
    mean_seconds: float
    p50_seconds: float
    p95_seconds: float
    max_seconds: float


def _percentile(ordered: list[float], quantile: float) -> float:
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    if lower >= len(ordered) - 1:
        return ordered[-1]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower])


def summarize_durations(durations: Iterable[float]) -> DurationSummary:
    values = sorted(durations)
    if not values:
        return DurationSummary(
            count=0,
            total_seconds=0.0,
            mean_seconds=0.0,
            p50_seconds=0.0,
            p95_seconds=0.0,
            max_seconds=0.0,
        )
    total = sum(values)
    return DurationSummary(
        count=len(values),
        total_seconds=total,
        mean_seconds=total / len(values),
        p50_seconds=_percentile(values, 0.5),
        p95_seconds=_percentile(values, 0.95),
        max_seconds=values[-1],
    )
