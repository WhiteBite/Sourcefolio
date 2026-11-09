from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_normalize_datetime)]


class ContractModel(BaseModel):
    """Strict external shape with no silent unknown-field acceptance."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ActorKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    IMPORT = "import"


class Provenance(ContractModel):
    """How a record was produced; not a claim that its content is true."""

    actor_kind: ActorKind
    actor_id: NonEmptyStr
    method: NonEmptyStr
    version: NonEmptyStr | None = None
    model_id: NonEmptyStr | None = None


class Approval(ContractModel):
    """Explicit human approval for a record written by another actor."""

    approved_by: NonEmptyStr
    approved_at: UtcDateTime


def require_human_ownership(
    provenance: Provenance,
    approval: Approval | None,
    *,
    record_name: str,
) -> None:
    if provenance.actor_kind != ActorKind.USER and approval is None:
        raise ValueError(f"{record_name} requires a user author or explicit user approval")


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def content_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"
