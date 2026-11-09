from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from pathlib import Path

_README_CANDIDATES = ("README.md", "README.rst", "README.txt")
_MAX_README_BYTES = 256 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024


class FileSystemRepoReader:
    def read_readme(self, repo_path: str) -> str | None:
        root = Path(repo_path)
        for name in _README_CANDIDATES:
            path = root / name
            try:
                if not path.is_file():
                    continue
                with path.open("rb") as stream:
                    data = stream.read(_MAX_README_BYTES)
            except OSError:
                continue
            return data.decode("utf-8", errors="replace")
        return None

    def read_manifest(self, repo_path: str) -> Mapping[str, object] | None:
        root = Path(repo_path)
        pyproject = root / "pyproject.toml"
        try:
            if pyproject.is_file() and pyproject.stat().st_size <= _MAX_MANIFEST_BYTES:
                with pyproject.open("rb") as stream:
                    parsed = tomllib.load(stream)
                if isinstance(parsed, dict):
                    return parsed
        except (OSError, ValueError):
            pass
        package = root / "package.json"
        try:
            if package.is_file() and package.stat().st_size <= _MAX_MANIFEST_BYTES:
                with package.open("rb") as stream:
                    parsed = json.loads(stream.read(_MAX_MANIFEST_BYTES).decode("utf-8"))
                if isinstance(parsed, dict):
                    return parsed
        except (OSError, ValueError):
            pass
        return None
