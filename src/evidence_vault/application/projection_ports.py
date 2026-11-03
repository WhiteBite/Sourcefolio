from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from evidence_vault.application.projection_types import GeneratedDocument


class ProjectionStore(Protocol):
    def replace_namespace(
        self,
        namespace: str,
        documents: Iterable[GeneratedDocument],
    ) -> int:
        """Replace only a machine-owned namespace and return its document count."""
        ...
