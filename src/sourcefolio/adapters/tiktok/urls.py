from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from sourcefolio.domain.source import SourceIdentity, SourceKind

_PATH_ID = re.compile(r"/(?:video|photo)/(\d+)(?:/|$)")
_ALLOWED_ROOTS = ("tiktok.com", "tiktokv.com")


def _allowed_host(host: str) -> bool:
    return any(host == root or host.endswith(f".{root}") for root in _ALLOWED_ROOTS)


def source_from_tiktok_url(value: str) -> SourceIdentity:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("TikTok URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("TikTok URL must not contain userinfo")

    host = (parsed.hostname or "").lower().rstrip(".")
    if not _allowed_host(host):
        raise ValueError(f"unsupported TikTok host: {host or '<missing>'}")

    path = re.sub(r"/{2,}", "/", parsed.path)
    if path != "/":
        path = path.rstrip("/")
    match = _PATH_ID.search(path)
    external_id = match.group(1) if match else None
    kind = SourceKind.OTHER
    if match and "/photo/" in match.string:
        kind = SourceKind.SLIDESHOW
    elif match:
        kind = SourceKind.VIDEO

    if host in {"tiktok.com", "m.tiktok.com"}:
        host = "www.tiktok.com"
    canonical_url = urlunsplit(("https", host, path, "", ""))
    return SourceIdentity(
        platform="tiktok",
        kind=kind,
        external_id=external_id,
        canonical_url=canonical_url,
    )
