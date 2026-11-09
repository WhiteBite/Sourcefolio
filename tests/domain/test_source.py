from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sourcefolio.domain.source import (
    CaptureStatus,
    SourceIdentity,
    SourceKind,
    SourcePacket,
    source_id_for,
)


def test_source_id_prefers_stable_external_id_over_url() -> None:
    first = SourceIdentity(
        platform="TIKTOK",
        kind=SourceKind.VIDEO,
        external_id="123",
        canonical_url="https://example.test/first",
    )
    second = SourceIdentity(
        platform="tiktok",
        kind=SourceKind.VIDEO,
        external_id="123",
        canonical_url="https://example.test/second",
    )

    assert source_id_for(first) == source_id_for(second)


def test_source_requires_an_identity_locator() -> None:
    with pytest.raises(ValidationError, match="external_id or canonical_url"):
        SourceIdentity(platform="manual", kind=SourceKind.TEXT)


def test_capture_is_idempotent_and_ids_are_content_derived(
    source: SourceIdentity,
    collector,
    captured_at,
    artifact_ref,
) -> None:
    first = SourcePacket.create(
        source=source,
        captured_at=captured_at,
        collector=collector,
        artifacts=(artifact_ref(),),
    )
    second = SourcePacket.create(
        source=source,
        captured_at=captured_at,
        collector=collector,
        artifacts=(artifact_ref(),),
    )

    assert first == second
    assert first.capture_id.startswith("cap_")


def test_capture_id_cannot_survive_content_edit(
    source: SourceIdentity,
    collector,
    captured_at,
    artifact_ref,
) -> None:
    packet = SourcePacket.create(
        source=source,
        captured_at=captured_at,
        collector=collector,
        artifacts=(artifact_ref(),),
    )
    changed = packet.model_dump()
    changed["title"] = "silent edit"

    with pytest.raises(ValidationError, match="capture_id does not match"):
        SourcePacket(**changed)


def test_partial_capture_requires_limitations(
    source: SourceIdentity,
    collector,
    captured_at,
    artifact_ref,
) -> None:
    with pytest.raises(ValidationError, match="partial capture requires"):
        SourcePacket.create(
            source=source,
            captured_at=captured_at,
            collector=collector,
            artifacts=(artifact_ref(),),
            status=CaptureStatus.PARTIAL,
        )


def test_complete_capture_cannot_hide_limitations(
    source: SourceIdentity,
    collector,
    captured_at,
    artifact_ref,
) -> None:
    with pytest.raises(ValidationError, match="complete capture cannot"):
        SourcePacket.create(
            source=source,
            captured_at=captured_at,
            collector=collector,
            artifacts=(artifact_ref(),),
            limitations=("missing slide",),
        )


def test_capture_requires_at_least_one_artifact(
    source: SourceIdentity,
    collector,
    captured_at,
) -> None:
    with pytest.raises(ValidationError):
        SourcePacket.create(
            source=source,
            captured_at=captured_at,
            collector=collector,
            artifacts=(),
        )


def test_capture_time_is_normalized_to_utc(
    source: SourceIdentity,
    collector,
    artifact_ref,
) -> None:
    moscow = timezone(timedelta(hours=3))
    packet = SourcePacket.create(
        source=source,
        captured_at=datetime(2026, 11, 3, 15, 0, tzinfo=moscow),
        collector=collector,
        artifacts=(artifact_ref(),),
    )

    assert packet.captured_at == datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    assert packet.captured_at.tzinfo == UTC
