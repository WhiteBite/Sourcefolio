from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue

from sourcefolio.adapters.filesystem.atomic import write_atomic_bytes


class FileSystemProjectFitIndex:
    def __init__(self, derived_root: Path) -> None:
        self._path = derived_root / "project-fit" / "index.json"

    def load(self) -> Mapping[str, JsonValue] | None:
        if not self._path.is_file():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def save(self, payload: Mapping[str, JsonValue]) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        write_atomic_bytes(self._path, data)
