from __future__ import annotations

import random

import pytest

from evidence_vault.application.capture_types import RetryPolicy


def test_backoff_is_exponential_capped_and_deterministic_without_jitter() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=0.5,
        max_delay_seconds=1.0,
        jitter_ratio=0,
    )
    random_source = random.Random(0)

    assert policy.delay(1, random_source) == 0.5
    assert policy.delay(2, random_source) == 1.0
    assert policy.delay(3, random_source) == 1.0


def test_jitter_stays_within_the_configured_band() -> None:
    policy = RetryPolicy(
        base_delay_seconds=1.0,
        max_delay_seconds=1.0,
        jitter_ratio=0.2,
    )

    for seed in range(20):
        delay = policy.delay(1, random.Random(seed))
        assert 0.8 <= delay <= 1.2


def test_invalid_retry_policy_values_fail_at_construction() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_seconds=-1)
    with pytest.raises(ValueError):
        RetryPolicy(jitter_ratio=2)
