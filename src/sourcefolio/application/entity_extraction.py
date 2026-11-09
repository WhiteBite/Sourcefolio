from __future__ import annotations

import re

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


def find_repositories(text: str) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for match in _REPOSITORY.finditer(text):
        host = match.group(1).lower()
        owner = _clean_owner(match.group(2))
        repository = _clean_repository(match.group(3))
        if not owner or not repository:
            continue
        found.setdefault(f"https://{host}.com/{owner}/{repository}", None)
    return tuple(found)


def find_hashtags(text: str) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for match in _HASHTAG.finditer(text):
        found.setdefault(match.group(1), None)
    return tuple(found)
