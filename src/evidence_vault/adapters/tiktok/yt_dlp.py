from __future__ import annotations

import asyncio
import hashlib
import json
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

_MEDIA_SUFFIXES = {
    ".mp4",
    ".webm",
    ".mkv",
    ".mov",
    ".m4a",
    ".mp3",
    ".aac",
    ".opus",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_AUDIO_SUFFIXES = {".m4a", ".mp3", ".aac", ".opus"}
_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_TRANSIENT_LOG = re.compile(
    r"timed out|timeout|connection reset|temporarily unavailable|"
    r"http error 429|http error 5\d\d|network|ssl|unexpected eof",
    re.IGNORECASE,
)
_INFO_ALLOWLIST = {
    "id",
    "title",
    "description",
    "uploader",
    "uploader_id",
    "channel",
    "channel_id",
    "webpage_url",
    "original_url",
    "extractor",
    "extractor_key",
    "duration",
    "timestamp",
    "upload_date",
    "view_count",
    "like_count",
    "comment_count",
    "repost_count",
    "ext",
    "format_id",
    "width",
    "height",
    "fps",
    "vcodec",
    "acodec",
}


@dataclass(frozen=True)
class YtDlpConfig:
    executable: str = "yt-dlp"
    temp_root: Path = Path("data/tmp/yt-dlp")
    format_selector: str = "best"
    site_limit: int = 2
    total_timeout_seconds: float = 300.0
    socket_timeout_seconds: int = 30
    max_artifact_bytes: int = 2 * 1024**3
    min_free_bytes: int = 512 * 1024**2

    def __post_init__(self) -> None:
        if self.site_limit < 1:
            raise ValueError("site_limit must be at least 1")
        if self.total_timeout_seconds <= 0 or self.socket_timeout_seconds <= 0:
            raise ValueError("yt-dlp timeouts must be positive")
        if self.max_artifact_bytes < 1 or self.min_free_bytes < 0:
            raise ValueError("invalid byte limits")
        if not self.format_selector.strip():
            raise ValueError("format_selector cannot be empty")


class YtDlpTikTokAdapter:
    def __init__(
        self,
        config: YtDlpConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or YtDlpConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.config.temp_root.mkdir(parents=True, exist_ok=True)
        self._site_semaphore = asyncio.Semaphore(self.config.site_limit)
        self._version_lock = asyncio.Lock()
        self._version: str | None = None
        self._version_checked = False
        self._executable_unavailable = False
        operation_fingerprint = canonical_json(
            {
                "adapter": "yt-dlp-tiktok/v1",
                "format_selector": self.config.format_selector,
                "max_artifact_bytes": self.config.max_artifact_bytes,
            }
        )
        digest = hashlib.sha256(operation_fingerprint.encode("utf-8")).hexdigest()[:16]
        self._operation_key = f"yt-dlp-tiktok/v1:{digest}"

    @property
    def operation_key(self) -> str:
        return self._operation_key

    @property
    def provenance(self) -> Provenance:
        return Provenance(
            actor_kind=ActorKind.TOOL,
            actor_id="yt-dlp",
            method="async-subprocess-capture",
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
            raise CaptureError("unable to start yt-dlp executable")
        async with self._site_semaphore:
            temporary_directory = tempfile.TemporaryDirectory(
                dir=self.config.temp_root,
                prefix="capture-",
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
        if shutil.disk_usage(self.config.temp_root).free < self.config.min_free_bytes:
            raise CaptureError("insufficient free disk space for media capture")
        if task.source.canonical_url is None:
            raise CaptureError("task has no canonical TikTok URL")
        try:
            source_from_tiktok_url(task.source.canonical_url)
        except ValueError as exc:
            raise CaptureError("invalid TikTok task URL") from exc

        log_path = directory / "process.log"
        command = (
            self.config.executable,
            "--no-playlist",
            "--no-progress",
            "--retries",
            "0",
            "--fragment-retries",
            "0",
            "--socket-timeout",
            str(self.config.socket_timeout_seconds),
            "--max-filesize",
            str(self.config.max_artifact_bytes),
            "--format",
            self.config.format_selector,
            "--write-info-json",
            "--paths",
            str(directory),
            "--output",
            "source.%(ext)s",
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
                    raise CaptureError("yt-dlp capture timed out", retryable=True) from None
        except OSError as exc:
            raise CaptureError("unable to start yt-dlp executable") from exc

        if process.returncode != 0:
            log_tail = self._read_tail(log_path)
            raise CaptureError(
                f"yt-dlp exited with status {process.returncode}",
                retryable=_TRANSIENT_LOG.search(log_tail) is not None,
            )

        info_files = sorted(directory.glob("*.info.json"))
        if len(info_files) != 1:
            raise CaptureError("yt-dlp did not produce exactly one info JSON")
        try:
            info = json.loads(info_files[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptureError("cannot parse yt-dlp info JSON") from exc
        if not isinstance(info, dict):
            raise CaptureError("yt-dlp info JSON is not an object")
        if info.get("_type") in {"playlist", "multi_video"}:
            raise CaptureError("playlist capture is not a single-source operation")

        source = self._source_from_info(info, task)
        media_files = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in _MEDIA_SUFFIXES
        )
        for media_file in media_files:
            if media_file.stat().st_size > self.config.max_artifact_bytes:
                raise CaptureError("downloaded artifact exceeds the configured byte limit")
            if media_file.stat().st_size == 0:
                raise CaptureError("yt-dlp produced an empty media artifact")

        safe_info = {key: info[key] for key in _INFO_ALLOWLIST if key in info}
        metadata_path = directory / "metadata.json"
        metadata_path.write_text(
            json.dumps(safe_info, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        limitations: list[str] = []
        has_video = any(
            path.suffix.lower() not in _IMAGE_SUFFIXES | _AUDIO_SUFFIXES for path in media_files
        )
        if source.kind == SourceKind.SLIDESHOW or (media_files and not has_video):
            limitations.append(
                "slideshow requires a dedicated resolver; only returned media was archived"
            )
        if not media_files:
            limitations.append("yt-dlp returned metadata but no media artifact")

        artifacts: list[ArtifactPayload] = []
        try:
            for media_file in media_files:
                artifacts.append(self._media_payload(media_file))
            artifacts.append(
                ArtifactPayload(
                    role=ArtifactRole.AUTHOR_METADATA,
                    media_type="application/json",
                    stream=metadata_path.open("rb"),
                    original_filename="yt-dlp-metadata.json",
                )
            )
            return AdapterCapture(
                source=source,
                captured_at=self._clock(),
                artifacts=tuple(artifacts),
                title=info.get("title") if isinstance(info.get("title"), str) else None,
                metadata={
                    "downloader": "yt-dlp",
                    "media_file_count": len(media_files),
                },
                limitations=tuple(limitations),
                satisfied=not limitations,
                cleanup=cleanup,
            )
        except Exception:
            for artifact in artifacts:
                artifact.stream.close()
            raise

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

    @staticmethod
    def _source_from_info(info: dict[str, Any], task: CaptureTask) -> SourceIdentity:
        candidate = info.get("webpage_url") or info.get("original_url")
        if not isinstance(candidate, str):
            candidate = task.source.canonical_url
        if not isinstance(candidate, str):
            raise CaptureError("yt-dlp returned no source URL")
        try:
            source = source_from_tiktok_url(candidate)
        except ValueError as exc:
            raise CaptureError("yt-dlp returned an invalid TikTok source URL") from exc
        info_id = info.get("id")
        if info_id is None:
            raise CaptureError("yt-dlp returned no external source id")
        external_id = str(info_id)
        if source.external_id is not None and source.external_id != external_id:
            raise CaptureError("downloaded id does not match the resolved source URL")
        kind = source.kind
        if kind == SourceKind.OTHER:
            kind = SourceKind.SLIDESHOW if "/photo/" in candidate else SourceKind.VIDEO
        return SourceIdentity(
            platform="tiktok",
            kind=kind,
            external_id=external_id,
            canonical_url=source.canonical_url,
        )

    @staticmethod
    def _media_payload(path: Path) -> ArtifactPayload:
        suffix = path.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            role = ArtifactRole.ORIGINAL_IMAGE
        elif suffix in _AUDIO_SUFFIXES:
            role = ArtifactRole.ORIGINAL_AUDIO
        else:
            role = ArtifactRole.ORIGINAL_VIDEO
        media_type = _MEDIA_TYPES[suffix]
        return ArtifactPayload(
            role=role,
            media_type=media_type,
            stream=path.open("rb"),
            original_filename=path.name,
        )
