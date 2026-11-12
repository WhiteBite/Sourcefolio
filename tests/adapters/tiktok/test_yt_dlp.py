from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import write_fake_executable
from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.sqlite import SQLiteCaptureState
from sourcefolio.adapters.tiktok.yt_dlp import YtDlpConfig, YtDlpTikTokAdapter
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.capture_batch import CaptureBatchRunner
from sourcefolio.application.capture_types import (
    BatchItemStatus,
    CaptureError,
    RetryPolicy,
)
from sourcefolio.domain.capture import CaptureTask
from sourcefolio.domain.source import (
    ArtifactRole,
    CaptureStatus,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def task(kind: SourceKind = SourceKind.VIDEO) -> CaptureTask:
    media_kind = "photo" if kind == SourceKind.SLIDESHOW else "video"
    url = f"https://www.tiktok.com/@example/{media_kind}/123"
    return CaptureTask.create(
        source=SourceIdentity(
            platform="tiktok",
            kind=kind,
            external_id="123",
            canonical_url=url,
        ),
        raw_entries=({"Link": url},),
    )


def executable(path: Path, behavior: str) -> Path:
    common = """import json
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("fake-yt-dlp-1.0")
    raise SystemExit(0)
directory = Path(sys.argv[sys.argv.index("--paths") + 1])
url = sys.argv[-1]
"""
    behaviors = {
        "success": """
(directory / "source.mp4").write_bytes(b"downloaded-video")
info = {
    "id": "123",
    "title": "A useful video",
    "description": "caption",
    "webpage_url": url,
    "extractor_key": "TikTok",
    "http_headers": {"Cookie": "secret-cookie"},
    "url": "https://signed.test/secret-token",
    "thumbnail": "https://signed.test/thumbnail-secret",
    "formats": [{"url": "secret-format-url"}],
}
(directory / "source.info.json").write_text(json.dumps(info), encoding="utf-8")
""",
        "transient": """
sys.stderr.write("HTTP Error 429: Too Many Requests")
raise SystemExit(1)
""",
        "private": """
sys.stderr.write("Private video is unavailable")
raise SystemExit(1)
""",
        "timeout": """
time.sleep(30)
""",
        "photo": """
(directory / "source.jpg").write_bytes(b"image")
info = {"id": "123", "title": "Slides", "webpage_url": url}
(directory / "source.info.json").write_text(json.dumps(info), encoding="utf-8")
""",
        "metadata": """
info = {"id": "123", "title": "No media", "webpage_url": url}
(directory / "source.info.json").write_text(json.dumps(info), encoding="utf-8")
""",
        "large": """
(directory / "source.mp4").write_bytes(b"1234567890")
info = {"id": "123", "webpage_url": url}
(directory / "source.info.json").write_text(json.dumps(info), encoding="utf-8")
""",
        "cover": """
(directory / "source.mp4").write_bytes(b"downloaded-video")
if "--write-thumbnail" in sys.argv:
    (directory / "source.jpg").write_bytes(b"cover-image")
info = {"id": "123", "title": "A useful video", "webpage_url": url}
(directory / "source.info.json").write_text(json.dumps(info), encoding="utf-8")
""",
    }
    return write_fake_executable(path, common + behaviors[behavior])


def config(path: Path, executable_path: Path, **changes) -> YtDlpConfig:
    base = YtDlpConfig(
        executable=str(executable_path),
        temp_root=path / "tmp",
        total_timeout_seconds=2,
        min_free_bytes=0,
        site_limit=1,
    )
    return replace(base, **changes)


def adapter(path: Path, behavior: str, **changes) -> YtDlpTikTokAdapter:
    return YtDlpTikTokAdapter(
        config(path, executable(path / "fake-yt-dlp", behavior), **changes),
        clock=lambda: NOW,
    )


def test_subprocess_capture_archives_sanitized_media_and_metadata(tmp_path: Path) -> None:
    media_adapter = adapter(tmp_path, "success")
    archive_root = tmp_path / "archive"
    artifact_store = FileSystemArtifactStore(archive_root)
    packets = FileSystemSourcePacketRepository(archive_root)
    archive = ArchiveCapture(artifacts=artifact_store, packets=packets)
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        runner = CaptureBatchRunner(
            adapter=media_adapter,
            archive=archive,
            state=state,
            retry_policy=RetryPolicy(base_delay_seconds=0, jitter_ratio=0),
            clock=lambda: NOW,
        )
        result = asyncio.run(runner.run([task()]))[0]

        assert result.status == BatchItemStatus.SUCCEEDED
        packet = packets.get(result.source_id, result.capture_id)
        assert packet is not None
        assert packet.status == CaptureStatus.COMPLETE
        assert packet.title == "A useful video"
        assert packet.collector.version == "fake-yt-dlp-1.0"
        video = next(
            artifact
            for artifact in packet.artifacts
            if artifact.role == ArtifactRole.ORIGINAL_VIDEO
        )
        with artifact_store.open(video.artifact_id) as stream:
            assert stream.read() == b"downloaded-video"
        for artifact in packet.artifacts:
            if artifact.role == ArtifactRole.AUTHOR_METADATA:
                with artifact_store.open(artifact.artifact_id) as stream:
                    content = stream.read()
                assert b"secret-cookie" not in content
                assert b"secret-token" not in content
                assert b"thumbnail-secret" not in content
                assert b"secret-format-url" not in content
        assert list((tmp_path / "tmp").iterdir()) == []
        assert not any(
            artifact.role == ArtifactRole.ORIGINAL_IMAGE for artifact in packet.artifacts
        )


def test_video_capture_archives_the_platform_cover_as_original_image(tmp_path: Path) -> None:
    media_adapter = adapter(tmp_path, "cover")

    capture = asyncio.run(media_adapter.capture(task()))
    try:
        assert capture.satisfied is True
        assert capture.source.kind == SourceKind.VIDEO
        roles = [artifact.role for artifact in capture.artifacts]
        assert roles.count(ArtifactRole.ORIGINAL_VIDEO) == 1
        image = next(
            artifact
            for artifact in capture.artifacts
            if artifact.role == ArtifactRole.ORIGINAL_IMAGE
        )
        assert image.media_type == "image/jpeg"
        assert image.stream.read() == b"cover-image"
    finally:
        capture.close()
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.parametrize(
    ("behavior", "retryable"),
    [("transient", True), ("private", False)],
)
def test_process_failures_have_explicit_retry_semantics(
    tmp_path: Path,
    behavior: str,
    retryable: bool,
) -> None:
    media_adapter = adapter(tmp_path, behavior)

    with pytest.raises(CaptureError) as caught:
        asyncio.run(media_adapter.capture(task()))

    assert caught.value.retryable is retryable
    assert "exited with status 1" in str(caught.value)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_timeout_kills_process_and_is_retryable(tmp_path: Path) -> None:
    media_adapter = adapter(tmp_path, "timeout", total_timeout_seconds=0.05)

    with pytest.raises(CaptureError) as caught:
        asyncio.run(media_adapter.capture(task()))

    assert caught.value.retryable is True
    assert "timed out" in str(caught.value)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_slideshow_is_explicitly_partial(tmp_path: Path) -> None:
    media_adapter = adapter(tmp_path, "photo")

    capture = asyncio.run(media_adapter.capture(task(SourceKind.SLIDESHOW)))
    try:
        assert capture.satisfied is False
        assert capture.source.kind == SourceKind.SLIDESHOW
        assert "dedicated resolver" in capture.limitations[0]
        assert capture.artifacts[0].role == ArtifactRole.ORIGINAL_IMAGE
        assert capture.artifacts[0].media_type == "image/jpeg"
    finally:
        capture.close()
    assert list((tmp_path / "tmp").iterdir()) == []


def test_metadata_only_process_result_is_not_called_complete(tmp_path: Path) -> None:
    media_adapter = adapter(tmp_path, "metadata")

    capture = asyncio.run(media_adapter.capture(task()))
    try:
        assert capture.satisfied is False
        assert "no media artifact" in capture.limitations[0]
    finally:
        capture.close()


def test_post_download_size_limit_is_enforced(tmp_path: Path) -> None:
    media_adapter = adapter(tmp_path, "large", max_artifact_bytes=5)

    with pytest.raises(CaptureError, match="exceeds the configured byte limit"):
        asyncio.run(media_adapter.capture(task()))


def test_operation_key_depends_on_output_config_not_timeouts(tmp_path: Path) -> None:
    executable_path = executable(tmp_path / "fake-yt-dlp", "success")
    first = YtDlpTikTokAdapter(config(tmp_path, executable_path, total_timeout_seconds=1))
    second = YtDlpTikTokAdapter(config(tmp_path, executable_path, total_timeout_seconds=99))
    different_format = YtDlpTikTokAdapter(
        config(tmp_path, executable_path, format_selector="worst")
    )

    assert first.operation_key == second.operation_key
    assert first.operation_key != different_format.operation_key


def test_missing_executable_fails_safely_without_a_traceback_leak(tmp_path: Path) -> None:
    media_adapter = YtDlpTikTokAdapter(
        YtDlpConfig(
            executable=str(tmp_path / "does-not-exist"),
            temp_root=tmp_path / "tmp",
            min_free_bytes=0,
        )
    )

    with pytest.raises(CaptureError) as caught:
        asyncio.run(media_adapter.capture(task()))

    assert caught.value.retryable is False
    assert str(caught.value) == "unable to start yt-dlp executable"
