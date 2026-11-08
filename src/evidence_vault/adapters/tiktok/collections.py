from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, ValidationError

from evidence_vault.adapters.tiktok.urls import source_from_tiktok_url
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.common import ContractModel, canonical_json
from evidence_vault.domain.source import (
    CollectionMembership,
    SourceIdentity,
    source_id_for,
)


class CollectionCaptureFormatError(ValueError):
    pass


class _CollectionCaptureModel(ContractModel):
    model_config = ConfigDict(extra="ignore")


class CollectionCaptureAccount(_CollectionCaptureModel):
    unique_id: str | None = None
    sec_uid: str | None = None
    uid: str | None = None


class CollectionCaptureStats(_CollectionCaptureModel):
    plays: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None


class CollectionCaptureComment(_CollectionCaptureModel):
    id: str | None = None
    text: str = ""
    author: str = ""
    likes: int | None = None
    created_at: str | None = None


class CollectionCaptureItem(_CollectionCaptureModel):
    video_id: str | None = None
    type: str = "video"
    url: str | None = None
    author: str = ""
    desc: str = ""
    created_at: str | None = None
    stats: CollectionCaptureStats = Field(default_factory=CollectionCaptureStats)
    images: list[str] = Field(default_factory=list)
    cover: str | None = None
    comments: list[CollectionCaptureComment] = Field(default_factory=list)


class CollectionCaptureCollection(_CollectionCaptureModel):
    collection_id: str | None = None
    name: str = ""
    expected: int = 0
    actual: int = 0
    items: list[CollectionCaptureItem] = Field(default_factory=list)


class CollectionCaptureRun(_CollectionCaptureModel):
    started_at: str | None = None
    finished_at: str | None = None
    mode: str | None = None
    totals: dict[str, JsonValue] = Field(default_factory=dict)


class CollectionCaptureDocument(_CollectionCaptureModel):
    schema_version: Literal["collection-capture/v1"]
    captured_at: str
    platform: str
    account: CollectionCaptureAccount
    collections: list[CollectionCaptureCollection] = Field(default_factory=list)
    run: CollectionCaptureRun | None = None


@dataclass(frozen=True)
class CollectionCaptureIssue:
    collection_index: int
    item_index: int | None
    reason: str


@dataclass(frozen=True)
class CollectionCaptureParseResult:
    tasks: tuple[CaptureTask, ...]
    entries_read: int
    issues: tuple[CollectionCaptureIssue, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class _TaskAccumulator:
    source: SourceIdentity
    raw_entries: list[dict[str, JsonValue]] = field(default_factory=list)
    collections: dict[tuple[str, str | None], CollectionMembership] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class CollectionCaptureParser:
    def parse(self, path: Path) -> CollectionCaptureParseResult:
        try:
            document = CollectionCaptureDocument.model_validate_json(path.read_bytes())
        except ValidationError as exc:
            raise CollectionCaptureFormatError(
                f"invalid collection-capture document: {exc}"
            ) from exc
        return self._build_tasks(document)

    def _build_tasks(self, document: CollectionCaptureDocument) -> CollectionCaptureParseResult:
        grouped: dict[str, _TaskAccumulator] = {}
        issues: list[CollectionCaptureIssue] = []
        warnings: list[str] = []
        entries_read = 0

        for collection_index, collection in enumerate(document.collections):
            membership = self._membership(collection, collection_index)
            if collection.expected > 0 and collection.expected != len(collection.items):
                warnings.append(
                    f"collection {collection_index} expected {collection.expected} items "
                    f"but captured {len(collection.items)}"
                )
            for item_index, item in enumerate(collection.items):
                entries_read += 1
                if not item.url:
                    issues.append(
                        CollectionCaptureIssue(collection_index, item_index, "missing item url")
                    )
                    continue
                try:
                    source = source_from_tiktok_url(item.url)
                except ValueError as exc:
                    issues.append(CollectionCaptureIssue(collection_index, item_index, str(exc)))
                    continue
                source_id = source_id_for(source)
                accumulator = grouped.setdefault(source_id, _TaskAccumulator(source=source))
                accumulator.source = min((accumulator.source, source), key=canonical_json)
                accumulator.raw_entries.append(item.model_dump(mode="json"))
                accumulator.collections[(membership.name, membership.external_id)] = membership
                if not accumulator.metadata:
                    accumulator.metadata = self._item_metadata(item)

        tasks = tuple(
            CaptureTask.create(
                source=accumulator.source,
                saved_at=None,
                collections=tuple(
                    sorted(
                        accumulator.collections.values(),
                        key=lambda item: (item.name, item.external_id or ""),
                    )
                ),
                raw_entries=tuple(sorted(accumulator.raw_entries, key=canonical_json)),
                metadata=accumulator.metadata,
            )
            for accumulator in grouped.values()
        )
        return CollectionCaptureParseResult(
            tasks=tasks,
            entries_read=entries_read,
            issues=tuple(issues),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _membership(
        collection: CollectionCaptureCollection,
        collection_index: int,
    ) -> CollectionMembership:
        name = (
            collection.name.strip() or collection.collection_id or f"collection-{collection_index}"
        )
        return CollectionMembership(name=name, external_id=collection.collection_id or None)

    @staticmethod
    def _item_metadata(item: CollectionCaptureItem) -> dict[str, JsonValue]:
        return {
            "import": "collection_capture",
            "parser_version": "1",
            "platform": "tiktok",
            "video_id": item.video_id,
            "type": item.type,
            "post_created_at": item.created_at,
            "author": item.author,
            "desc": item.desc,
            "images": list(item.images),
            "cover": item.cover,
            "stats": item.stats.model_dump(mode="json"),
            "comments": [comment.model_dump(mode="json") for comment in item.comments],
        }
