from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import write_fake_executable
from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.sqlite import SQLiteCaptureState
from evidence_vault.adapters.tiktok.gallery_dl import (
    GalleryDlConfig,
    GalleryDlTikTokSlidesAdapter,
)
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.capture_batch import CaptureBatchRunner
from evidence_vault.application.capture_types import (
    BatchItemStatus,
    CaptureError,
    RetryPolicy,
)
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.source import (
    ArtifactRole,
    CaptureStatus,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def task(kind: SourceKind = SourceKind.SLIDESHOW) -> CaptureTask:
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
    print("fake-gallery-dl-1.0")
    raise SystemExit(0)
directory = Path(sys.argv[sys.argv.index("--directory") + 1])

def write_slide(number, expected=2):
    image = directory / f"slide-{number:02}.jpg"
    image.write_bytes(f"image-{number}".encode())
    images = [{"imageURL": {"urlList": ["signed-image-secret"]}} for _ in range(expected or 0)]
    info = {
        "id": "123",
        "desc": "A slideshow",
        "num": number,
        "width": 1080,
        "height": 1920,
        "imagePost": {"images": images},
        "image": {"urlList": ["signed-image-secret"]},
        "music": {"playUrl": "signed-audio-secret"},
        "http_headers": {"Cookie": "secret-cookie"},
    }
    if expected is None:
        del info["imagePost"]
    Path(str(image) + ".json").write_text(json.dumps(info), encoding="utf-8")
"""
    behaviors = {
        "success": """
write_slide(1)
write_slide(2)
""",
        "partial": """
write_slide(1, expected=2)
""",
        "unknown_count": """
write_slide(1, expected=None)
""",
        "nonimage": """
write_slide(1, expected=1)
(directory / "slide-00.mp3").write_bytes(b"unexpected")
""",
        "large": """
write_slide(1, expected=1)
write_slide(2, expected=1)
""",
        "transient": """
sys.stderr.write("HTTP Error 429: Too Many Requests")
raise SystemExit(1)
""",
        "timeout": """
time.sleep(30)
""",
    }
    return write_fake_executable(path, common + behaviors[behavior])


def config(path: Path, executable_path: Path, **changes) -> GalleryDlConfig:
    base = GalleryDlConfig(
        executable=str(executable_path),
        temp_root=path / "tmp",
        total_timeout_seconds=2,
        min_free_bytes=0,
        site_limit=1,
    )
    return replace(base, **changes)


def adapter(path: Path, behavior: str, **changes) -> GalleryDlTikTokSlidesAdapter:
    return GalleryDlTikTokSlidesAdapter(
        config(path, executable(path / "fake-gallery-dl", behavior), **changes),
        clock=lambda: NOW,
    )


def test_dedicated_resolver_archives_complete_ordered_slideshow(tmp_path: Path) -> None:
    slides_adapter = adapter(tmp_path, "success")
    archive_root = tmp_path / "archive"
    artifact_store = FileSystemArtifactStore(archive_root)
    packets = FileSystemSourcePacketRepository(archive_root)
    archive = ArchiveCapture(artifacts=artifact_store, packets=packets)
    with SQLiteCaptureState(tmp_path / "control.sqlite") as state:
        runner = CaptureBatchRunner(
            adapter=slides_adapter,
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
    assert packet.title == "A slideshow"
    assert packet.collector.version == "fake-gallery-dl-1.0"
    images = [
        artifact for artifact in packet.artifacts if artifact.role == ArtifactRole.ORIGINAL_IMAGE
    ]
    assert [artifact.original_filename for artifact in images] == [
        "slide-01.jpg",
        "slide-02.jpg",
    ]
    for artifact in packet.artifacts:
        if artifact.role == ArtifactRole.AUTHOR_METADATA:
            with artifact_store.open(artifact.artifact_id) as stream:
                content = stream.read()
            if artifact.original_filename == "gallery-dl-slides-metadata.json":
                assert b"signed-image-secret" not in content
                assert b"signed-audio-secret" not in content
                assert b"secret-cookie" not in content
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.parametrize(
    ("behavior", "limitation"),
    [
        ("partial", "resolved 1 of 2"),
        ("unknown_count", "total slide count unavailable"),
    ],
)
def test_unverified_or_incomplete_slideshow_is_partial(
    tmp_path: Path,
    behavior: str,
    limitation: str,
) -> None:
    capture = asyncio.run(adapter(tmp_path, behavior).capture(task()))
    try:
        assert capture.satisfied is False
        assert limitation in capture.limitations[0]
        assert len(capture.artifacts) == 2
    finally:
        capture.close()


def test_unexpected_non_image_media_fails_safely(tmp_path: Path) -> None:
    with pytest.raises(CaptureError, match="unexpected non-image"):
        asyncio.run(adapter(tmp_path, "nonimage").capture(task()))


def test_total_size_limit_is_enforced(tmp_path: Path) -> None:
    with pytest.raises(CaptureError, match="total byte limit"):
        asyncio.run(
            adapter(
                tmp_path,
                "large",
                max_slide_bytes=100,
                max_total_bytes=10,
            ).capture(task())
        )


def test_process_failures_and_timeout_have_explicit_retry_semantics(tmp_path: Path) -> None:
    with pytest.raises(CaptureError) as caught:
        asyncio.run(adapter(tmp_path / "429", "transient").capture(task()))
    assert caught.value.retryable is True

    with pytest.raises(CaptureError) as caught:
        asyncio.run(
            adapter(
                tmp_path / "timeout",
                "timeout",
                total_timeout_seconds=0.05,
            ).capture(task())
        )
    assert caught.value.retryable is True
    assert "timed out" in str(caught.value)


def test_resolver_rejects_video_and_missing_executable(tmp_path: Path) -> None:
    slides_adapter = adapter(tmp_path / "video", "success")
    with pytest.raises(CaptureError, match="requires a slideshow"):
        asyncio.run(slides_adapter.capture(task(SourceKind.VIDEO)))

    missing = GalleryDlTikTokSlidesAdapter(
        GalleryDlConfig(
            executable=str(tmp_path / "does-not-exist"),
            temp_root=tmp_path / "missing-tmp",
            min_free_bytes=0,
        )
    )
    with pytest.raises(CaptureError, match="unable to start"):
        asyncio.run(missing.capture(task()))


def test_operation_key_depends_on_slide_output_limits(tmp_path: Path) -> None:
    executable_path = executable(tmp_path / "fake-gallery-dl", "success")
    first = GalleryDlTikTokSlidesAdapter(config(tmp_path, executable_path))
    same = GalleryDlTikTokSlidesAdapter(config(tmp_path, executable_path, total_timeout_seconds=99))
    different = GalleryDlTikTokSlidesAdapter(config(tmp_path, executable_path, max_slides=10))

    assert first.operation_key == same.operation_key
    assert first.operation_key != different.operation_key
