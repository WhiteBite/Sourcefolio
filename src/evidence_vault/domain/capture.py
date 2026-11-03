from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, model_validator

from evidence_vault.domain.common import (
    ContractModel,
    NonEmptyStr,
    UtcDateTime,
    content_id,
)
from evidence_vault.domain.source import (
    CollectionMembership,
    SourceId,
    SourceIdentity,
    source_id_for,
)

TaskId = Annotated[str, StringConstraints(pattern=r"^task_[0-9a-f]{64}$")]
OperationHash = Annotated[str, StringConstraints(pattern=r"^op_[0-9a-f]{64}$")]


class CaptureTaskContent(ContractModel):
    """A saved-item discovery record; it is not yet a media capture."""

    schema_version: Literal["capture-task/v1"] = "capture-task/v1"
    source: SourceIdentity
    saved_at: UtcDateTime | None = None
    collections: tuple[CollectionMembership, ...] = ()
    raw_entries: tuple[dict[str, JsonValue], ...] = Field(min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CaptureTask(CaptureTaskContent):
    task_id: TaskId

    @model_validator(mode="after")
    def id_matches_source(self) -> CaptureTask:
        if self.task_id != self._task_id_for_source(self.source):
            raise ValueError("task_id does not match source identity")
        return self

    @staticmethod
    def _task_id_for_source(source: SourceIdentity) -> str:
        return content_id(
            "task",
            {"operation": "source-capture/v1", "source_id": source_id_for(source)},
        )

    @property
    def source_id(self) -> SourceId:
        return source_id_for(self.source)

    @property
    def input_hash(self) -> str:
        return content_id(
            "input",
            self.model_dump(mode="json", exclude={"task_id"}),
        )

    def operation_hash(
        self,
        adapter_operation_key: NonEmptyStr,
        *,
        input_hash: str | None = None,
    ) -> OperationHash:
        return content_id(
            "op",
            {
                "adapter_operation": adapter_operation_key,
                "task_input": input_hash or self.input_hash,
            },
        )

    @classmethod
    def create(
        cls,
        *,
        source: SourceIdentity,
        raw_entries: tuple[dict[str, JsonValue], ...],
        saved_at: UtcDateTime | None = None,
        collections: tuple[CollectionMembership, ...] = (),
        metadata: dict[str, JsonValue] | None = None,
    ) -> CaptureTask:
        content = CaptureTaskContent(
            source=source,
            saved_at=saved_at,
            collections=collections,
            raw_entries=raw_entries,
            metadata=metadata or {},
        )
        return cls(
            **content.model_dump(),
            task_id=cls._task_id_for_source(source),
        )
