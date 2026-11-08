from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from evidence_vault.adapters.github.repos import (
    GitHubRepoClient,
    GitHubRepoConfig,
    GitHubRepoError,
    canonical_payload,
)

RAW = {
    "full_name": "owner/repo",
    "html_url": "https://github.com/owner/repo",
    "description": "A local-first tool",
    "stargazers_count": 42,
    "language": "Python",
    "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
    "archived": False,
    "pushed_at": "2026-11-01T10:00:00Z",
    "topics": ["ai", "local-first"],
    "noise": "ignored",
}


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, size: int = -1) -> bytes:
        return self._data if size < 0 else self._data[:size]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def install(monkeypatch, *, data: bytes = b"{}", error: Exception | None = None) -> dict:
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        if error is not None:
            raise error
        return FakeResponse(data)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/repos/owner/repo", status, "err", {}, None
    )


def test_fetch_maps_canonical_fields(monkeypatch) -> None:
    captured = install(monkeypatch, data=json.dumps(RAW).encode("utf-8"))

    result = GitHubRepoClient().fetch("owner", "repo")

    assert result == {
        "full_name": "owner/repo",
        "html_url": "https://github.com/owner/repo",
        "description": "A local-first tool",
        "stars": 42,
        "language": "Python",
        "license": "MIT",
        "archived": False,
        "pushed_at": "2026-11-01T10:00:00Z",
        "topics": ["ai", "local-first"],
    }
    request = captured["request"]
    assert request.full_url == "https://api.github.com/repos/owner/repo"
    headers = dict(request.header_items())
    assert headers["User-agent"] == "evidence-vault"
    assert "Authorization" not in headers
    assert captured["timeout"] == 30.0


def test_fetch_sends_bearer_token(monkeypatch) -> None:
    captured = install(monkeypatch, data=json.dumps(RAW).encode("utf-8"))

    GitHubRepoClient(GitHubRepoConfig(token="t0ken")).fetch("owner", "repo")

    headers = dict(captured["request"].header_items())
    assert headers["Authorization"] == "Bearer t0ken"
    assert "t0ken" not in repr(GitHubRepoConfig(token="t0ken"))


def test_fetch_maps_missing_license_and_topics(monkeypatch) -> None:
    install(monkeypatch, data=json.dumps({"full_name": "owner/repo", "license": None}).encode())

    result = GitHubRepoClient().fetch("owner", "repo")

    assert result["license"] == ""
    assert result["topics"] == []
    assert result["stars"] is None


def test_fetch_404_is_clear_error(monkeypatch) -> None:
    install(monkeypatch, error=http_error(404))

    with pytest.raises(GitHubRepoError, match=r"not found \(404\)"):
        GitHubRepoClient().fetch("owner", "missing")


@pytest.mark.parametrize("status", [403, 429])
def test_fetch_rate_limit_is_clear_error(monkeypatch, status: int) -> None:
    install(monkeypatch, error=http_error(status))

    with pytest.raises(GitHubRepoError, match="rate limit"):
        GitHubRepoClient().fetch("owner", "repo")


def test_fetch_other_status_is_clear_error(monkeypatch) -> None:
    install(monkeypatch, error=http_error(500))

    with pytest.raises(GitHubRepoError, match="status 500"):
        GitHubRepoClient().fetch("owner", "repo")


def test_fetch_enforces_byte_limit(monkeypatch) -> None:
    install(monkeypatch, data=b"{" + b" " * 64)
    client = GitHubRepoClient(GitHubRepoConfig(max_response_bytes=32))

    with pytest.raises(GitHubRepoError, match="byte limit"):
        client.fetch("owner", "repo")


def test_fetch_rejects_invalid_json(monkeypatch) -> None:
    install(monkeypatch, data=b"not json")

    with pytest.raises(GitHubRepoError, match="not valid JSON"):
        GitHubRepoClient().fetch("owner", "repo")


def test_fetch_rejects_non_object_json(monkeypatch) -> None:
    install(monkeypatch, data=b"[]")

    with pytest.raises(GitHubRepoError, match="not a JSON object"):
        GitHubRepoClient().fetch("owner", "repo")


def test_fetch_wraps_network_errors(monkeypatch) -> None:
    install(monkeypatch, error=urllib.error.URLError("dns failure"))

    with pytest.raises(GitHubRepoError, match="URLError"):
        GitHubRepoClient().fetch("owner", "repo")


def test_fetch_wraps_timeout(monkeypatch) -> None:
    install(monkeypatch, error=TimeoutError("timed out"))

    with pytest.raises(GitHubRepoError, match="TimeoutError"):
        GitHubRepoClient().fetch("owner", "repo")


def test_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="timeout"):
        GitHubRepoConfig(timeout_seconds=0)
    with pytest.raises(ValueError, match="user_agent"):
        GitHubRepoConfig(user_agent=" ")
    with pytest.raises(ValueError, match="base_url"):
        GitHubRepoConfig(base_url="")
    with pytest.raises(ValueError, match="max_response_bytes"):
        GitHubRepoConfig(max_response_bytes=0)


def test_canonical_payload_ignores_unsupported_types() -> None:
    result = canonical_payload(
        {
            "full_name": "owner/repo",
            "stargazers_count": True,
            "description": 42,
            "topics": "not-a-list",
        }
    )

    assert result["stars"] is None
    assert result["description"] == ""
    assert result["topics"] == []
