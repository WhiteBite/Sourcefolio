from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from evidence_vault.application.capture_types import AdapterCapture
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.common import ActorKind, Provenance


class MetadataOnlyCaptureAdapter:
    """Archive the saved-item index now; never pretend that media was fetched."""

    operation_key = "metadata-only/v1"
    provenance = Provenance(
        actor_kind=ActorKind.TOOL,
        actor_id="metadata-only-capture",
        method="saved-index-archive",
        version="1",
    )

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    async def capture(self, task: CaptureTask) -> AdapterCapture:
        return AdapterCapture(
            source=task.source,
            captured_at=self._clock(),
            limitations=("media capture was not requested",),
            satisfied=True,
        )
