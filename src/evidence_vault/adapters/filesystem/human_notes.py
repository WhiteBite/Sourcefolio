from __future__ import annotations

import os
import warnings
from collections.abc import Iterable
from pathlib import Path, PurePosixPath


class FileSystemHumanNoteStore:
    """Human-owned notes outside the generated namespace; never overwritten."""

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
