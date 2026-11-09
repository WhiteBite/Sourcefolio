from __future__ import annotations

import pytest

from sourcefolio.adapters.tiktok.urls import source_from_tiktok_url
from sourcefolio.domain.source import SourceKind


def test_video_url_extracts_stable_id_and_drops_tracking() -> None:
    source = source_from_tiktok_url(
        "https://m.tiktok.com/@creator/video/1234567890/?utm_source=x&foo=bar#fragment"
    )

    assert source.external_id == "1234567890"
    assert source.kind == SourceKind.VIDEO
    assert source.canonical_url == "https://www.tiktok.com/@creator/video/1234567890"


def test_photo_url_is_a_slideshow() -> None:
    source = source_from_tiktok_url("https://www.tiktok.com/@creator/photo/987654321")

    assert source.external_id == "987654321"
    assert source.kind == SourceKind.SLIDESHOW


def test_short_link_does_not_invent_an_external_id() -> None:
    source = source_from_tiktok_url("https://vm.tiktok.com/AbCd123/?share=1")

    assert source.external_id is None
    assert source.kind == SourceKind.OTHER
    assert source.canonical_url == "https://vm.tiktok.com/AbCd123"


@pytest.mark.parametrize(
    "url",
    [
        "https://tiktok.com.evil.test/@x/video/123",
        "https://evil.test/@x/video/123",
        "file:///tmp/video.mp4",
        "https://user:secret@tiktok.com/@x/video/123",
    ],
)
def test_unsafe_or_unsupported_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        source_from_tiktok_url(url)
