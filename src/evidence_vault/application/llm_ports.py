from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import JsonValue


class ChatClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the assistant message content for one chat completion."""
        ...


class LlmCacheStore(Protocol):
    def load(self, key: str) -> Mapping[str, JsonValue] | None:
        """Return the cached enrichment for a content key, or None when absent."""
        ...

    def save(self, key: str, payload: Mapping[str, JsonValue]) -> None:
        """Persist the enrichment for a content key, replacing any previous entry."""
        ...
