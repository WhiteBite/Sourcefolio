from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue

from sourcefolio.adapters.filesystem.atomic import write_atomic_bytes


class FileSystemLlmCache:
    def __init__(self, derived_root: Path, namespace: str = "llm") -> None:
        self._root = derived_root / namespace

    def _path(self, key: str) -> Path:
        return self._root / f"{key}.json"

    def load(self, key: str) -> Mapping[str, JsonValue] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def save(self, key: str, payload: Mapping[str, JsonValue]) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        write_atomic_bytes(self._path(key), data)
