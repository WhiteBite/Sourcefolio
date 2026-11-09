from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from sourcefolio.domain.source import SourcePacket

SAVED_INDEX_KEY = "saved_index"


def saved_index(packet: SourcePacket) -> Mapping[str, JsonValue]:
    value = packet.metadata.get(SAVED_INDEX_KEY)
    if isinstance(value, dict):
        return value
    return {}


def saved_str(source: Mapping[str, JsonValue], key: str) -> str | None:
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def saved_int(source: Mapping[str, JsonValue], key: str) -> int | None:
    value = source.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def saved_comments(source: Mapping[str, JsonValue]) -> tuple[Mapping[str, JsonValue], ...]:
    value = source.get("comments")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def saved_texts(packet: SourcePacket) -> tuple[str, ...]:
    source = saved_index(packet)
    texts: list[str] = []
    description = saved_str(source, "desc")
    if description is not None:
        texts.append(description)
    for comment in saved_comments(source):
        text = saved_str(comment, "text")
        if text is not None:
            texts.append(text)
    return tuple(texts)
