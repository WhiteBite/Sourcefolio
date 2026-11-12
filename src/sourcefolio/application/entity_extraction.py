from __future__ import annotations

import re
from dataclasses import dataclass

_REPOSITORY = re.compile(
    r"(?:https?://)?(?:www\.)?(github|gitlab)\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_HASHTAG = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)


def _clean_owner(value: str) -> str:
    return value.rstrip(".-")


def _clean_repository(value: str) -> str:
    value = value.rstrip(".-")
    if value.endswith(".git"):
        value = value[:-4]
    return value


@dataclass(frozen=True)
class RepositoryMatch:
    url: str
    start_char: int
    end_char: int


def iter_repositories(text: str) -> tuple[RepositoryMatch, ...]:
    found: dict[str, RepositoryMatch] = {}
    for match in _REPOSITORY.finditer(text):
        host = match.group(1).lower()
        owner = _clean_owner(match.group(2))
        repository = _clean_repository(match.group(3))
        if not owner or not repository:
            continue
        url = f"https://{host}.com/{owner}/{repository}"
        found.setdefault(url, RepositoryMatch(url, match.start(), match.end()))
    return tuple(found.values())


def find_repositories(text: str) -> tuple[str, ...]:
    return tuple(match.url for match in iter_repositories(text))


def find_hashtags(text: str) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for match in _HASHTAG.finditer(text):
        found.setdefault(match.group(1), None)
    return tuple(found)
