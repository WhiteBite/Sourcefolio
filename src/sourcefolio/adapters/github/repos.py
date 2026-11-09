from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import JsonValue

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_USER_AGENT = "sourcefolio"


class GitHubRepoError(RuntimeError):
    """A safe repository fetch failure; the token is never included in the message."""


@dataclass(frozen=True)
class GitHubRepoConfig:
    token: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 30.0
    max_response_bytes: int = 4 * 1024**2
    user_agent: str = DEFAULT_USER_AGENT

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if not self.user_agent.strip():
            raise ValueError("user_agent is required")


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _as_str_list(value: object) -> list[JsonValue]:
    if not isinstance(value, list):
        return []
    return [text for text in (_as_str(item) for item in value) if text]


def canonical_payload(data: Mapping[str, object]) -> dict[str, JsonValue]:
    license_value = data.get("license")
    spdx = license_value.get("spdx_id") if isinstance(license_value, dict) else None
    return {
        "full_name": _as_str(data.get("full_name")),
        "html_url": _as_str(data.get("html_url")),
        "description": _as_str(data.get("description")),
        "stars": _as_int(data.get("stargazers_count")),
        "language": _as_str(data.get("language")),
        "license": _as_str(spdx),
        "archived": data.get("archived") is True,
        "pushed_at": _as_str(data.get("pushed_at")),
        "topics": _as_str_list(data.get("topics")),
    }


def _http_error_message(status: int) -> str:
    if status == 404:
        return "repository not found (404): missing, renamed, or private"
    if status in (403, 429):
        return (
            f"GitHub API refused the request (status {status}): rate limit reached; "
            "pass --token or set GITHUB_TOKEN"
        )
    return f"GitHub API request failed with status {status}"


class GitHubRepoClient:
    def __init__(self, config: GitHubRepoConfig | None = None) -> None:
        self._config = config or GitHubRepoConfig()

    def fetch(self, owner: str, repository: str) -> Mapping[str, JsonValue]:
        config = self._config
        url = f"{config.base_url.rstrip('/')}/repos/{owner}/{repository}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": config.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = (config.token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
                raw = response.read(config.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise GitHubRepoError(_http_error_message(exc.code)) from exc
        except OSError as exc:
            raise GitHubRepoError(f"GitHub API request failed: {type(exc).__name__}") from exc
        if len(raw) > config.max_response_bytes:
            raise GitHubRepoError("GitHub API response exceeds the configured byte limit")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubRepoError("GitHub API response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise GitHubRepoError("GitHub API response is not a JSON object")
        return canonical_payload(data)
