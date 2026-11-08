"""Optional OpenAI-compatible chat adapters."""

from evidence_vault.adapters.llm.openai_chat import (
    LlmClientError,
    OpenAIChatClient,
    OpenAIChatConfig,
)

__all__ = ["LlmClientError", "OpenAIChatClient", "OpenAIChatConfig"]
