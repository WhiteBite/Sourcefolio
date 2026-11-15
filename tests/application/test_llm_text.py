from __future__ import annotations

from pathlib import Path

import pytest

from sourcefolio.adapters.filesystem.llm_cache import FileSystemLlmCache
from sourcefolio.application.llm_text import extract_json_object


def test_llm_cache_load_returns_none_for_corrupt_entry(tmp_path: Path) -> None:
    cache = FileSystemLlmCache(tmp_path)
    path = tmp_path / "llm" / "k.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert cache.load("k") is None


def test_extract_json_object_handles_messy_text() -> None:
    messy = (
        "Sure! Here is the result:\n"
        "```json\n"
        '{"title": "a {brace} in a string", "summary": "ok", '
        '"category": "ai", "entities": [], "links": [], "tags": ["x"]}\n'
        "```\n"
        "Let me know if you need more."
    )

    parsed = extract_json_object(messy)

    assert parsed["title"] == "a {brace} in a string"
    assert parsed["tags"] == ["x"]


def test_extract_json_object_rejects_text_without_object() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json_object("no json here at all")
