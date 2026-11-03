from __future__ import annotations

import pytest
from pydantic import ValidationError

from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.source import SourceIdentity, SourceKind


def test_task_id_is_stable_but_operation_hash_depends_on_adapter() -> None:
    source = SourceIdentity(
        platform="tiktok",
        kind=SourceKind.VIDEO,
        external_id="123",
    )
    task = CaptureTask.create(source=source, raw_entries=({"Link": "https://example"},))

    assert (
        task.task_id
        == CaptureTask.create(
            source=source,
            raw_entries=({"Link": "https://different"},),
        ).task_id
    )
    assert task.operation_hash("metadata/v1") != task.operation_hash("media/v1")


def test_task_id_cannot_be_changed_independently() -> None:
    task = CaptureTask.create(
        source=SourceIdentity(platform="manual", kind=SourceKind.TEXT, external_id="1"),
        raw_entries=({"text": "hello"},),
    )
    changed = task.model_dump()
    changed["raw_entries"] = ({"text": "changed"},)
    changed["task_id"] = "task_" + "0" * 64

    with pytest.raises(ValidationError, match="task_id does not match"):
        CaptureTask(**changed)
