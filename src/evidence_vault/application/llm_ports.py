from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import JsonValue

from evidence_vault.domain.source import CaptureId


class ChatClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the assistant message content for one chat completion."""
        ...


class LlmCacheStore(Protocol):
    def load(self, capture_id: CaptureId) -> Mapping[str, JsonValue] | None:
        """Return the cached enrichment for a capture, or None when absent."""
        ...

    def save(self, capture_id: CaptureId, payload: Mapping[str, JsonValue]) -> None:
        """Persist the enrichment for a capture, replacing any previous entry."""
        ...
