from __future__ import annotations

import json
from typing import Any

from sourcefolio.application.saved_index import saved_comments, saved_index, saved_str
from sourcefolio.domain.source import SourcePacket

_MAX_COMMENTS = 10


def _balanced_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    for start, character in enumerate(text):
        if character != "{":
            continue
        candidate = _balanced_object(text, start)
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found in LLM response")


def build_source_prompt(packet: SourcePacket) -> str:
    saved = saved_index(packet)
    lines = [
        f"URL: {packet.source.canonical_url or 'unknown'}",
        f"Author: {saved_str(saved, 'author') or 'unknown'}",
        f"Description: {saved_str(saved, 'desc') or ''}",
    ]
    comments = saved_comments(saved)
    if comments:
        lines.append("Comments:")
        for comment in comments[:_MAX_COMMENTS]:
            text = saved_str(comment, "text")
            if text:
                lines.append(f"- {text}")
    return "\n".join(lines)
