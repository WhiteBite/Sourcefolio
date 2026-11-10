from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol

from sourcefolio.application.projection_types import GeneratedDocument


class ProjectionStore(Protocol):
    def replace_namespace(
        self,
        namespace: str,
        documents: Iterable[GeneratedDocument],
    ) -> int:
        """Replace only a machine-owned namespace and return its document count."""
        ...


class HumanNoteStore(Protocol):
    def create_if_absent(self, relative_path: str, content: str) -> bool:
        """Create a human-owned note only when absent; return True if created."""
        ...

    def update_properties(
        self,
        relative_path: str,
        properties: Mapping[str, object | None],
    ) -> bool:
        """Replace only the given frontmatter keys; None removes a key. Body stays untouched."""
        ...


class HumanNoteReader(Protocol):
    @property
    def root(self) -> Path:
        """Filesystem directory the reader is anchored at."""
        ...

    def iter_notes(self) -> Iterable[tuple[str, str]]:
        """Yield (posix_relpath, content) for every human-owned note."""
        ...

    def read(self, relative_path: str) -> str | None:
        """Return note content, or None when the note is absent."""
        ...
