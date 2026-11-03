from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evidence_vault.adapters.tiktok.urls import source_from_tiktok_url
from evidence_vault.application.capture_types import AdapterCapture, CaptureError
from evidence_vault.application.commands import ArtifactPayload
from evidence_vault.domain.capture import CaptureTask
from evidence_vault.domain.common import ActorKind, Provenance, canonical_json
from evidence_vault.domain.source import ArtifactRole, SourceIdentity, SourceKind

_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_OTHER_MEDIA_SUFFIXES = {
    ".mp4",
    ".webm",
    ".mkv",
    ".mov",
    ".m4a",
    ".mp3",
    ".aac",
    ".opus",
    ".gif",
}
_TRANSIENT_LOG = re.compile(
    r"timed out|timeout|connection reset|temporarily unavailable|"
    r"http error 429|http error 5\d\d|network|ssl|unexpected eof",
    re.IGNORECASE,
)
_SLIDE_ALLOWLIST = {
    "id",
    "desc",
    "title",
    "createTime",
    "date",
    "user",
    "post_type",
    "type",
    "num",
    "width",
    "height",
    "filename",
    "extension",
    "category",
    "subcategory",
}


def _is_safe_scalar(value: object) -> bool:
    if value is None or isinstance(value, str | bool | int):
        return True
    return isinstance(value, float) and math.isfinite(value)


@dataclass(frozen=True)
class GalleryDlConfig:
    executable: str = "gallery-dl"
    temp_root: Path = Path("data/tmp/gallery-dl")
    site_limit: int = 2
    total_timeout_seconds: float = 300.0
    socket_timeout_seconds: float = 30.0
    max_slides: int = 50
    max_slide_bytes: int = 50 * 1024**2
    max_total_bytes: int = 500 * 1024**2
    max_metadata_bytes: int = 2 * 1024**2
    min_free_bytes: int = 512 * 1024**2

    def __post_init__(self) -> None:
        if self.site_limit < 1 or self.max_slides < 1:
            raise ValueError("site_limit and max_slides must be at least 1")
        if self.total_timeout_seconds <= 0 or self.socket_timeout_seconds <= 0:
            raise ValueError("gallery-dl timeouts must be positive")
        if (
            min(
                self.max_slide_bytes,
                self.max_total_bytes,
                self.max_metadata_bytes,
            )
            < 1
        ):
            raise ValueError("gallery-dl byte limits must be positive")
        if self.min_free_bytes < 0:
            raise ValueError("min_free_bytes cannot be negative")


@dataclass(frozen=True)
class _Slide:
    path: Path
    number: int
    metadata: dict[str, Any]


class GalleryDlTikTokSlidesAdapter:
    """Dedicated slideshow resolver; completeness requires verified slide inventory."""

    def __init__(
        self,
        config: GalleryDlConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or GalleryDlConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.config.temp_root.mkdir(parents=True, exist_ok=True)
        self._site_semaphore = asyncio.Semaphore(self.config.site_limit)
        self._version_lock = asyncio.Lock()
        self._version: str | None = None
        self._version_checked = False
        self._executable_unavailable = False
        fingerprint = canonical_json(
            {
                "adapter": "gallery-dl-tiktok-slides/v1",
                "max_slides": self.config.max_slides,
                "max_slide_bytes": self.config.max_slide_bytes,
                "max_total_bytes": self.config.max_total_bytes,
            }
        )
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
        self._operation_key = f"gallery-dl-tiktok-slides/v1:{digest}"

    @property
    def operation_key(self) -> str:
        return self._operation_key

    @property
    def provenance(self) -> Provenance:
        return Provenance(
            actor_kind=ActorKind.TOOL,
            actor_id="gallery-dl",
            method="async-subprocess-slideshow-capture",
            version=self._version,
        )

    async def _ensure_version(self) -> None:
        if self._version_checked:
            return
        async with self._version_lock:
            if self._version_checked:
                return
            try:
                process = await asyncio.create_subprocess_exec(
                    self.config.executable,
                    "--version",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError:
                self._executable_unavailable = True
                self._version_checked = True
                return
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            except TimeoutError:
                await self._kill_process(process)
                self._version_checked = True
                return
            if process.returncode == 0:
                version = stdout.decode("utf-8", errors="replace").strip()[:100]
                self._version = version or None
            self._version_checked = True

    async def capture(self, task: CaptureTask) -> AdapterCapture:
        await self._ensure_version()
        if self._executable_unavailable:
            raise CaptureError("unable to start gallery-dl executable")
        async with self._site_semaphore:
            temporary_directory = tempfile.TemporaryDirectory(
                dir=self.config.temp_root,
                prefix="slides-",
            )
            try:
                return await self._capture_in_directory(
                    task,
                    Path(temporary_directory.name),
                    temporary_directory.cleanup,
                )
            except Exception:
                temporary_directory.cleanup()
                raise

    async def _capture_in_directory(
        self,
        task: CaptureTask,
        directory: Path,
        cleanup: Callable[[], None],
    ) -> AdapterCapture:
        if task.source.kind != SourceKind.SLIDESHOW:
            raise CaptureError("gallery-dl slideshow resolver requires a slideshow task")
        if shutil.disk_usage(self.config.temp_root).free < self.config.min_free_bytes:
            raise CaptureError("insufficient free disk space for slideshow capture")
        if task.source.canonical_url is None:
            raise CaptureError("task has no canonical TikTok URL")
        try:
            source_from_tiktok_url(task.source.canonical_url)
        except ValueError as exc:
            raise CaptureError("invalid TikTok task URL") from exc

        log_path = directory / "process.log"
        command = (
            self.config.executable,
            "--config-ignore",
            "--no-input",
            "--no-mtime",
            "--no-colors",
            "--retries",
            "0",
            "--http-timeout",
            str(self.config.socket_timeout_seconds),
            "--filesize-max",
            str(self.config.max_slide_bytes),
            "--range",
            f"1-{self.config.max_slides}",
            "--cache-file",
            str(directory / "cache.sqlite3"),
            "-o",
            "extractor.tiktok.audio=false",
            "-o",
            "extractor.tiktok.videos=false",
            "-o",
            "extractor.tiktok.covers=false",
            "-o",
            "extractor.tiktok.subtitles=false",
            "--write-metadata",
            "--directory",
            str(directory),
            "--filename",
            "slide-{num:02}.{extension}",
            task.source.canonical_url,
        )
        try:
            with log_path.open("wb") as log_stream:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log_stream,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=self.config.total_timeout_seconds,
                    )
                except TimeoutError:
                    await self._kill_process(process)
                    raise CaptureError(
                        "gallery-dl slideshow capture timed out",
                        retryable=True,
                    ) from None
        except OSError as exc:
            raise CaptureError("unable to start gallery-dl executable") from exc

        if process.returncode != 0:
            log_tail = self._read_tail(log_path)
            raise CaptureError(
                f"gallery-dl exited with status {process.returncode}",
                retryable=_TRANSIENT_LOG.search(log_tail) is not None,
            )
        return self._collect(task, directory, cleanup)

    def _collect(
        self,
        task: CaptureTask,
        directory: Path,
        cleanup: Callable[[], None],
    ) -> AdapterCapture:
        unexpected = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in _OTHER_MEDIA_SUFFIXES
        ]
        if unexpected:
            raise CaptureError("gallery-dl returned unexpected non-image media")

        image_paths = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_TYPES
        ]
        if any(path.is_symlink() for path in image_paths):
            raise CaptureError("gallery-dl returned a symlinked image")
        if not image_paths:
            return AdapterCapture(
                source=task.source,
                captured_at=self._clock(),
                limitations=("gallery-dl returned no slideshow images",),
                satisfied=False,
                cleanup=cleanup,
            )

        slides: list[_Slide] = []
        total_bytes = 0
        expected_counts: set[int] = set()
        external_ids: set[str] = set()
        for path in image_paths:
            if path.stat().st_size == 0:
                raise CaptureError("gallery-dl produced an empty image")
            size = path.stat().st_size
            if size > self.config.max_slide_bytes:
                raise CaptureError("downloaded slide exceeds the configured byte limit")
            total_bytes += size
            if total_bytes > self.config.max_total_bytes:
                raise CaptureError("slideshow exceeds the configured total byte limit")
            info = self._read_metadata(Path(f"{path}.json"))
            number = info.get("num")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise CaptureError("gallery-dl metadata has no valid slide number")
            external_id = info.get("id")
            if (
                isinstance(external_id, bool)
                or not isinstance(external_id, str | int)
                or not str(external_id)
            ):
                raise CaptureError("gallery-dl metadata has no source id")
            external_ids.add(str(external_id))
            image_post = info.get("imagePost")
            if isinstance(image_post, dict) and isinstance(image_post.get("images"), list):
                expected_counts.add(len(image_post["images"]))
            safe = {
                key: value
                for key in _SLIDE_ALLOWLIST
                if key in info and _is_safe_scalar(value := info[key])
            }
            slides.append(_Slide(path=path, number=number, metadata=safe))

        if len(external_ids) != 1:
            raise CaptureError("gallery-dl returned inconsistent source ids")
        external_id = next(iter(external_ids))
        if task.source.external_id is not None and task.source.external_id != external_id:
            raise CaptureError("resolved slideshow id does not match the task")
        source = SourceIdentity(
            platform="tiktok",
            kind=SourceKind.SLIDESHOW,
            external_id=external_id,
            canonical_url=task.source.canonical_url,
        )
        slides.sort(key=lambda slide: slide.number)
        numbers = [slide.number for slide in slides]
        if len(set(numbers)) != len(numbers):
            raise CaptureError("gallery-dl returned duplicate slide numbers")

        limitations: list[str] = []
        expected_count: int | None = None
        if len(expected_counts) > 1:
            raise CaptureError("gallery-dl returned inconsistent slide inventories")
        if expected_counts:
            expected_count = next(iter(expected_counts))
            if expected_count != len(slides):
                limitations.append(
                    f"slideshow incomplete: resolved {len(slides)} of {expected_count} images"
                )
        else:
            limitations.append("total slide count unavailable; completeness cannot be verified")
        if numbers != list(range(1, len(slides) + 1)):
            limitations.append("slideshow numbering is not contiguous")

        metadata_path = directory / "resolved-metadata.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "resolver": "gallery-dl",
                    "expected_slide_count": expected_count,
                    "resolved_slide_count": len(slides),
                    "slides": [slide.metadata for slide in slides],
                },
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        title = next(
            (
                value
                for slide in slides
                for key in ("desc", "title")
                if isinstance((value := slide.metadata.get(key)), str) and value.strip()
            ),
            None,
        )
        artifacts: list[ArtifactPayload] = []
        try:
            for slide in slides:
                artifacts.append(
                    ArtifactPayload(
                        role=ArtifactRole.ORIGINAL_IMAGE,
                        media_type=_IMAGE_TYPES[slide.path.suffix.lower()],
                        stream=slide.path.open("rb"),
                        original_filename=slide.path.name,
                    )
                )
            artifacts.append(
                ArtifactPayload(
                    role=ArtifactRole.AUTHOR_METADATA,
                    media_type="application/json",
                    stream=metadata_path.open("rb"),
                    original_filename="gallery-dl-slides-metadata.json",
                )
            )
            return AdapterCapture(
                source=source,
                captured_at=self._clock(),
                artifacts=tuple(artifacts),
                title=title,
                metadata={
                    "downloader": "gallery-dl",
                    "resolved_slide_count": len(slides),
                    "expected_slide_count": expected_count,
                },
                limitations=tuple(limitations),
                satisfied=not limitations,
                cleanup=cleanup,
            )
        except Exception:
            for artifact in artifacts:
                artifact.stream.close()
            raise

    def _read_metadata(self, path: Path) -> dict[str, Any]:
        try:
            if path.stat().st_size > self.config.max_metadata_bytes:
                raise CaptureError("gallery-dl metadata exceeds the configured byte limit")
            value = json.loads(path.read_text(encoding="utf-8"))
        except CaptureError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptureError("cannot parse gallery-dl slide metadata") from exc
        if not isinstance(value, dict):
            raise CaptureError("gallery-dl slide metadata is not an object")
        return value

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if sys.platform != "win32":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
        except ProcessLookupError:
            pass
        await process.wait()

    @staticmethod
    def _read_tail(path: Path, limit: int = 64 * 1024) -> str:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace")
