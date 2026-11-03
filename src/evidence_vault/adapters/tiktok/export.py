from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import ijson
from pydantic import JsonValue

from evidence_vault.adapters.tiktok.urls import source_from_tiktok_url
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.common import canonical_json
from evidence_vault.domain.source import (
    CollectionMembership,
    SourceIdentity,
    source_id_for,
)

_JSON_LISTS = (
    "Activity.Favorite Videos.FavoriteVideoList",
    "Favorite Videos.FavoriteVideoList",
)
_LINK_KEYS = ("Link", "link", "VideoLink", "video_link")
_DATE_KEYS = ("Date", "date", "SavedAt", "saved_at")
_DEFAULT_COLLECTION_FIELDS = (
    "Collection",
    "CollectionName",
    "Collection Name",
    "Collections",
)


class ExportFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ExportIssue:
    entry_index: int
    reason: str


@dataclass(frozen=True)
class ExportParseResult:
    tasks: tuple[CaptureTask, ...]
    entries_read: int
    issues: tuple[ExportIssue, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class _TaskAccumulator:
    source: SourceIdentity
    raw_entries: list[dict[str, JsonValue]] = field(default_factory=list)
    collections: dict[tuple[str, str | None], CollectionMembership] = field(default_factory=dict)
    saved_dates: list[datetime] = field(default_factory=list)


def _json_value(value: Any) -> JsonValue:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ExportFormatError(f"unsupported JSON value: {type(value).__name__}")


def _first_value(entry: Mapping[str, JsonValue], keys: tuple[str, ...]) -> JsonValue:
    for key in keys:
        if key in entry:
            return entry[key]
    return None


def _iter_json_lists(path: Path, prefixes: tuple[str, ...]) -> Iterator[tuple[str, Any]]:
    """Stream only selected arrays while still distinguishing missing and empty lists."""

    with path.open("rb") as stream:
        active_array: str | None = None
        active_item: str | None = None
        builder: ijson.ObjectBuilder | None = None

        for prefix, event, value in ijson.parse(stream):
            if builder is not None:
                builder.event(event, value)
                if prefix == active_item and event == "end_map":
                    yield active_array or "", builder.value
                    builder = None
                    active_item = None
                continue

            if event == "start_array" and prefix in prefixes:
                # Empty lists are observable through an explicit sentinel.
                yield prefix, None
                active_array = prefix
                continue

            if active_array is None:
                continue
            if event == "end_array" and prefix == active_array:
                active_array = None
                continue
            if prefix == f"{active_array}.item" and event == "start_map":
                active_item = prefix
                builder = ijson.ObjectBuilder()
                builder.event(event, value)
            elif prefix == f"{active_array}.item" and event not in {
                "start_array",
                "end_array",
            }:
                raise ExportFormatError("favorite list entries must be JSON objects")


class TikTokExportParser:
    def __init__(
        self,
        *,
        naive_date_timezone: str | None = None,
        collection_fields: tuple[str, ...] = _DEFAULT_COLLECTION_FIELDS,
    ) -> None:
        self._timezone = ZoneInfo(naive_date_timezone) if naive_date_timezone else None
        self._collection_fields = collection_fields

    def parse(
        self,
        path: Path,
        *,
        file_format: Literal["json", "txt"] | None = None,
    ) -> ExportParseResult:
        selected_format = file_format or self._detect_format(path)
        if selected_format == "json":
            entries, list_path = self._read_json(path)
        else:
            entries = list(self._read_txt(path))
            list_path = "txt"
        return self._build_tasks(entries, list_path=list_path, file_format=selected_format)

    @staticmethod
    def _detect_format(path: Path) -> Literal["json", "txt"]:
        suffix = path.suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix in {".txt", ".text"}:
            return "txt"
        raise ExportFormatError("export format must be json or txt")

    @staticmethod
    def _read_json(path: Path) -> tuple[list[dict[str, JsonValue]], str]:
        found: dict[str, list[dict[str, JsonValue]]] = {}
        try:
            for prefix, value in _iter_json_lists(path, _JSON_LISTS):
                entries = found.setdefault(prefix, [])
                if value is None:
                    continue
                if not isinstance(value, Mapping):
                    raise ExportFormatError("favorite list entry must be an object")
                entries.append({str(key): _json_value(item) for key, item in value.items()})
        except ijson.JSONError as exc:
            raise ExportFormatError(f"invalid JSON export: {exc}") from exc

        for prefix in _JSON_LISTS:
            if prefix in found:
                return found[prefix], prefix
        supported = ", ".join(_JSON_LISTS)
        raise ExportFormatError(f"no supported Favorite Videos list found; tried: {supported}")

    @staticmethod
    def _read_txt(path: Path) -> Iterator[dict[str, JsonValue]]:
        current: dict[str, JsonValue] = {}

        def flush() -> dict[str, JsonValue] | None:
            nonlocal current
            if not current:
                return None
            result = current
            current = {}
            return result

        with path.open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, start=1):
                stripped = line.strip()
                if not stripped:
                    if (entry := flush()) is not None:
                        yield entry
                    continue
                if ":" not in stripped:
                    raise ExportFormatError(f"unrecognized TXT line {line_number}")
                key, value = (part.strip() for part in stripped.split(":", 1))
                if key in current and (entry := flush()) is not None:
                    yield entry
                current[key] = value
        if (entry := flush()) is not None:
            yield entry

    def _parse_date(self, value: JsonValue) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            if self._timezone is None:
                return None
            return parsed.replace(tzinfo=self._timezone)
        return parsed

    def _collections(self, entry: Mapping[str, JsonValue]) -> list[CollectionMembership]:
        result: list[CollectionMembership] = []
        for field_name in self._collection_fields:
            value = entry.get(field_name)
            if isinstance(value, str) and value.strip():
                result.append(CollectionMembership(name=value))
            elif isinstance(value, list):
                result.extend(
                    CollectionMembership(name=item)
                    for item in value
                    if isinstance(item, str) and item.strip()
                )
        return result

    def _build_tasks(
        self,
        entries: list[dict[str, JsonValue]],
        *,
        list_path: str,
        file_format: str,
    ) -> ExportParseResult:
        grouped: dict[str, _TaskAccumulator] = {}
        issues: list[ExportIssue] = []
        naive_dates = 0
        invalid_dates = 0

        for index, entry in enumerate(entries):
            link = _first_value(entry, _LINK_KEYS)
            if not isinstance(link, str) or not link.strip():
                issues.append(ExportIssue(index, "missing video link"))
                continue
            try:
                source = source_from_tiktok_url(link)
            except ValueError as exc:
                issues.append(ExportIssue(index, str(exc)))
                continue

            source_id = source_id_for(source)
            accumulator = grouped.setdefault(source_id, _TaskAccumulator(source=source))
            accumulator.source = min(
                (accumulator.source, source),
                key=canonical_json,
            )
            accumulator.raw_entries.append(entry)
            for collection in self._collections(entry):
                accumulator.collections[(collection.name, collection.external_id)] = collection

            raw_date = _first_value(entry, _DATE_KEYS)
            if isinstance(raw_date, str) and raw_date.strip():
                parsed_date = self._parse_date(raw_date)
                if parsed_date is None:
                    try:
                        datetime.fromisoformat(raw_date.strip().replace("Z", "+00:00"))
                    except ValueError:
                        invalid_dates += 1
                    else:
                        naive_dates += 1
                else:
                    accumulator.saved_dates.append(parsed_date)

        tasks: list[CaptureTask] = []
        for accumulator in grouped.values():
            raw_entries = tuple(
                sorted(accumulator.raw_entries, key=canonical_json),
            )
            collections = tuple(
                sorted(
                    accumulator.collections.values(),
                    key=lambda item: (item.name, item.external_id or ""),
                )
            )
            tasks.append(
                CaptureTask.create(
                    source=accumulator.source,
                    saved_at=min(accumulator.saved_dates) if accumulator.saved_dates else None,
                    collections=collections,
                    raw_entries=raw_entries,
                    metadata={
                        "import": "tiktok_favorites",
                        "format": file_format,
                        "list_path": list_path,
                        "parser_version": "1",
                    },
                )
            )

        warnings: list[str] = []
        if naive_dates:
            warnings.append(
                f"{naive_dates} naive dates preserved raw; pass an export timezone to normalize"
            )
        if invalid_dates:
            warnings.append(f"{invalid_dates} dates could not be parsed; raw values preserved")
        return ExportParseResult(
            tasks=tuple(tasks),
            entries_read=len(entries),
            issues=tuple(issues),
            warnings=tuple(warnings),
        )
