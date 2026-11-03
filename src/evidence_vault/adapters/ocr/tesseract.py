from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from evidence_vault.application.extraction_types import ExtractedText, ExtractionError
from evidence_vault.domain.common import ActorKind, Provenance, canonical_json
from evidence_vault.domain.extraction import EvidenceLocator, ExtractionKind, LocatorKind
from evidence_vault.domain.source import ArtifactRef, ArtifactRole

_CHUNK_SIZE = 1024 * 1024
_LANGUAGE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MEDIA_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass(frozen=True)
class TesseractConfig:
    executable: str = "tesseract"
    temp_root: Path = Path("data/tmp/tesseract")
    tessdata_dir: Path | None = None
    language: str = "eng+rus"
    page_segmentation_mode: int = 3
    timeout_seconds: float = 60.0
    max_input_bytes: int = 50 * 1024**2
    max_output_bytes: int = 1024**2
    min_free_bytes: int = 64 * 1024**2

    def __post_init__(self) -> None:
        languages = self.language.split("+")
        if not self.language.strip() or any(
            _LANGUAGE.fullmatch(language) is None for language in languages
        ):
            raise ValueError("invalid OCR language configuration")
        if not 0 <= self.page_segmentation_mode <= 13:
            raise ValueError("invalid Tesseract page segmentation mode")
        if self.timeout_seconds <= 0:
            raise ValueError("OCR timeout must be positive")
        if self.max_input_bytes < 1 or self.max_output_bytes < 1:
            raise ValueError("OCR byte limits must be positive")
        if self.min_free_bytes < 0:
            raise ValueError("min_free_bytes cannot be negative")


class TesseractOcrAdapter:
    def __init__(self, config: TesseractConfig | None = None) -> None:
        self._config = config or TesseractConfig()
        self._config.temp_root.mkdir(parents=True, exist_ok=True)
        self._prepare_lock = threading.Lock()
        self._prepared = False
        self._version: str | None = None
        self._language_data_fingerprint: str | None = None

    @property
    def kind(self) -> ExtractionKind:
        return ExtractionKind.OCR_TEXT

    @property
    def provenance(self) -> Provenance:
        return Provenance(
            actor_kind=ActorKind.TOOL,
            actor_id="tesseract",
            method="subprocess-ocr",
            version=self._version,
        )

    @property
    def config(self) -> dict[str, str | int]:
        return {
            "language": self._config.language,
            "language_data_sha256": self._language_data_fingerprint or "unverified",
            "page_segmentation_mode": self._config.page_segmentation_mode,
        }

    def prepare(self) -> None:
        if self._prepared:
            return
        with self._prepare_lock:
            if self._prepared:
                return
            try:
                result = subprocess.run(
                    [self._config.executable, "--version"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ExtractionError("unable to start Tesseract executable") from exc
            if result.returncode != 0:
                raise ExtractionError("Tesseract version check failed")
            lines = result.stdout.decode("utf-8", errors="replace").splitlines()
            self._version = lines[0].strip()[:200] if lines else "unknown"
            if self._config.tessdata_dir is not None:
                self._language_data_fingerprint = self._fingerprint_language_data()
            self._prepared = True

    def extract(
        self,
        *,
        artifact: ArtifactRef,
        content: BinaryIO,
        locator: EvidenceLocator,
    ) -> ExtractedText:
        self.prepare()
        if artifact.role != ArtifactRole.ORIGINAL_IMAGE or locator.kind != LocatorKind.SLIDE:
            raise ExtractionError("Tesseract OCR requires an original image and slide locator")
        media_type = artifact.media_type.split(";", 1)[0].strip().lower()
        suffix = _MEDIA_SUFFIXES.get(media_type)
        if suffix is None:
            raise ExtractionError("unsupported image media type for Tesseract OCR")
        if shutil.disk_usage(self._config.temp_root).free < self._config.min_free_bytes:
            raise ExtractionError("insufficient free disk space for OCR")

        with tempfile.TemporaryDirectory(
            dir=self._config.temp_root,
            prefix="ocr-",
        ) as temporary_directory:
            directory = Path(temporary_directory)
            input_path = directory / f"input{suffix}"
            self._write_bounded(content, input_path)
            output_path = directory / "output.txt"
            log_path = directory / "process.log"
            command = [self._config.executable]
            if self._config.tessdata_dir is not None:
                command.extend(("--tessdata-dir", str(self._config.tessdata_dir)))
            command.extend(
                (
                    str(input_path),
                    "stdout",
                    "-l",
                    self._config.language,
                    "--psm",
                    str(self._config.page_segmentation_mode),
                )
            )
            try:
                with output_path.open("wb") as output_stream, log_path.open("wb") as log_stream:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=output_stream,
                        stderr=log_stream,
                        start_new_session=True,
                    )
                    try:
                        process.wait(timeout=self._config.timeout_seconds)
                    except subprocess.TimeoutExpired:
                        self._kill_process(process)
                        raise ExtractionError("Tesseract OCR timed out") from None
            except OSError as exc:
                raise ExtractionError("unable to start Tesseract executable") from exc
            if process.returncode != 0:
                raise ExtractionError(f"Tesseract exited with status {process.returncode}")
            return self._read_output(output_path)

    def _fingerprint_language_data(self) -> str:
        if self._config.tessdata_dir is None:
            raise AssertionError("language data fingerprint requires tessdata_dir")
        digests: dict[str, str] = {}
        try:
            for language in self._config.language.split("+"):
                path = self._config.tessdata_dir / f"{language}.traineddata"
                if not path.is_file():
                    raise ExtractionError("Tesseract language data not found")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(_CHUNK_SIZE):
                        digest.update(chunk)
                digests[language] = digest.hexdigest()
        except OSError as exc:
            raise ExtractionError("cannot read Tesseract language data") from exc
        fingerprint = canonical_json(digests)
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    def _write_bounded(self, content: BinaryIO, path: Path) -> None:
        total = 0
        with path.open("wb") as output:
            while chunk := content.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > self._config.max_input_bytes:
                    raise ExtractionError("OCR input exceeds the configured byte limit")
                output.write(chunk)
        if total == 0:
            raise ExtractionError("OCR input is empty")

    def _read_output(self, path: Path) -> ExtractedText:
        with path.open("rb") as stream:
            data = stream.read(self._config.max_output_bytes + 1)
        if len(data) > self._config.max_output_bytes:
            raise ExtractionError("OCR output exceeds the configured byte limit")
        text = data.decode("utf-8", errors="replace").strip()
        limitations: list[str] = []
        if not text:
            limitations.append("OCR returned no text")
        if self._language_data_fingerprint is None:
            limitations.append("language data fingerprint unavailable")
        return ExtractedText(text=text, limitations=tuple(limitations))

    @staticmethod
    def _kill_process(process: subprocess.Popen[bytes]) -> None:
        if process.returncode is not None:
            return
        try:
            if sys.platform != "win32":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(
                    ("taskkill", "/F", "/T", "/PID", str(process.pid)),
                    capture_output=True,
                    check=False,
                )
        except ProcessLookupError:
            pass
        process.wait()
