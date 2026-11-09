from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class LlmClientError(RuntimeError):
    """A safe chat failure; the api key is never included in the message."""


@dataclass(frozen=True)
class OpenAIChatConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 120.0
    max_response_bytes: int = 4 * 1024**2
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if not self.api_key.strip():
            raise ValueError("api_key is required")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")


def _extract_content(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LlmClientError("LLM response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LlmClientError("LLM response is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmClientError("LLM response has no choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LlmClientError("LLM response has no message content")
    return content


class OpenAIChatClient:
    def __init__(self, config: OpenAIChatConfig) -> None:
        self._config = config

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": self._config.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self._config.temperature,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read(self._config.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise LlmClientError(f"LLM request failed with status {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LlmClientError("LLM request failed: network error") from exc
        if len(raw) > self._config.max_response_bytes:
            raise LlmClientError("LLM response exceeds the configured byte limit")
        return _extract_content(raw)
