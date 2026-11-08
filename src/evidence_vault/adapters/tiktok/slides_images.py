from __future__ import annotations

import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import IO

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_REFERER = "https://www.tiktok.com/"
_CHUNK_SIZE = 64 * 1024


class ImageFetchError(Exception):
    """A single-image download failure whose message is safe to persist."""


def _open_temp_stream() -> IO[bytes]:
    return tempfile.TemporaryFile()


@dataclass(frozen=True)
class SlidesImageConfig:
    timeout_seconds: float = 30.0
    max_bytes: int = 20 * 1024**2
    user_agent: str = _USER_AGENT
    referer: str = _REFERER

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")


class SlidesImageFetcher:
    """Stream one TikTok slideshow image to an anonymous temp file under a byte limit."""

    def __init__(self, config: SlidesImageConfig | None = None) -> None:
        self._config = config or SlidesImageConfig()

    def fetch(self, url: str) -> IO[bytes]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self._config.user_agent,
                "Referer": self._config.referer,
            },
        )
        try:
            response = urllib.request.urlopen(request, timeout=self._config.timeout_seconds)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ImageFetchError(f"image request failed: {type(exc).__name__}") from exc
        with response:
            stream = _open_temp_stream()
            total = 0
            try:
                while chunk := response.read(_CHUNK_SIZE):
                    total += len(chunk)
                    if total > self._config.max_bytes:
                        raise ImageFetchError("image exceeds the configured byte limit")
                    stream.write(chunk)
            except Exception:
                stream.close()
                raise
            if total == 0:
                stream.close()
                raise ImageFetchError("image response was empty")
            stream.seek(0)
            return stream
