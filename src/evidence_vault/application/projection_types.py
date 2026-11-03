from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedDocument:
    """A rebuildable document with a POSIX relative path inside one namespace."""

    path: str
    content: str
