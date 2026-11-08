from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


class FileSystemLlmCache:
    def __init__(self, derived_root: Path) -> None:
        self._root = derived_root / "llm"

    def _path(self, key: str) -> Path:
        return self._root / f"{key}.json"

    def load(self, key: str) -> Mapping[str, JsonValue] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("llm cache entry must be a JSON object")
        return data

    def save(self, key: str, payload: Mapping[str, JsonValue]) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        _write_atomic(self._path(key), data)
