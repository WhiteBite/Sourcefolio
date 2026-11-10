from __future__ import annotations

import os
import re
import tempfile
import warnings
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath

import yaml

_FRONTMATTER_BLOCK = re.compile(
    r"\A---\r?\n(?P<body>.*?)\r?\n---(?P<closing>\r?\n|\Z)",
    re.DOTALL,
)


class FileSystemHumanNoteStore:
    """Human-owned notes outside the generated namespace; bodies and unrequested keys are kept."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _relative_path(value: str) -> Path:
        if not value or "\\" in value:
            raise ValueError("human note path must be a non-empty POSIX relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("human note path escapes its root")
        return Path(*path.parts)

    def iter_notes(self) -> Iterable[tuple[str, str]]:
        if not self._root.is_dir():
            return
        paths = sorted(
            self._root.rglob("*.md"),
            key=lambda path: path.relative_to(self._root).as_posix(),
        )
        for path in paths:
            relative = path.relative_to(self._root).as_posix()
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as error:
                warnings.warn(f"skipping unreadable human note {relative}: {error}", stacklevel=2)
                continue
            yield relative, content

    def read(self, relative_path: str) -> str | None:
        path = self._root / self._relative_path(relative_path)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as error:
            warnings.warn(f"skipping unreadable human note {relative_path}: {error}", stacklevel=2)
            return None

    def create_if_absent(self, relative_path: str, content: str) -> bool:
        path = self._root / self._relative_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return True

    def update_properties(
        self,
        relative_path: str,
        properties: Mapping[str, object | None],
    ) -> bool:
        path = self._root / self._relative_path(relative_path)
        if not path.is_file():
            return False
        content = self.read(relative_path)
        if content is None:
            return False
        match = _FRONTMATTER_BLOCK.match(content)
        if match is None:
            merged: dict[str, object] = {}
            tail = content
            closing = "\n"
        else:
            try:
                loaded = yaml.safe_load(match.group("body"))
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"human note {relative_path} frontmatter is not valid YAML: {exc}"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(f"human note {relative_path} frontmatter is not a mapping")
            merged = {str(key): value for key, value in loaded.items()}
            tail = content[match.end() :]
            closing = match.group("closing")
        for key, value in properties.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        frontmatter = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False).strip()
        if match is None:
            updated = f"---\n{frontmatter}\n---\n{tail}"
        else:
            updated = f"---\n{frontmatter}\n---{closing}{tail}"
        self._replace_content(path, updated)
        return True

    @staticmethod
    def _replace_content(path: Path, content: str) -> None:
        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
