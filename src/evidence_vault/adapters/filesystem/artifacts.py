from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from evidence_vault.adapters.filesystem.atomic import file_sha256, install_temp_file
from evidence_vault.application.commands import ArtifactPayload
from evidence_vault.domain.source import ArtifactId, ArtifactRef

_CHUNK_SIZE = 1024 * 1024
_ARTIFACT_ID = re.compile(r"^art_([0-9a-f]{64})$")


class FileSystemArtifactStore:
    def __init__(self, archive_root: Path) -> None:
        self._root = archive_root
        self._temp_root = archive_root / ".tmp"
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def put(self, payload: ArtifactPayload) -> ArtifactRef:
        file_descriptor, temp_name = tempfile.mkstemp(
            dir=self._temp_root,
            prefix="capture-",
        )
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        size_bytes = 0

        try:
            with os.fdopen(file_descriptor, "wb") as temp_file:
                while chunk := payload.stream.read(_CHUNK_SIZE):
                    if not isinstance(chunk, bytes):
                        raise TypeError("artifact stream must return bytes")
                    digest.update(chunk)
                    size_bytes += len(chunk)
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            if size_bytes == 0:
                raise ValueError("empty artifacts are not valid evidence")

            sha256 = digest.hexdigest()
            final_path = self._root / "artifacts" / sha256[:2] / sha256
            install_temp_file(temp_path, final_path, expected_sha256=sha256)
            return ArtifactRef(
                artifact_id=f"art_{sha256}",
                role=payload.role,
                media_type=payload.media_type,
                size_bytes=size_bytes,
                sha256=sha256,
                original_filename=payload.original_filename,
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _path(self, artifact_id: ArtifactId) -> Path:
        match = _ARTIFACT_ID.fullmatch(artifact_id)
        if match is None:
            raise ValueError("invalid artifact_id")
        digest = match.group(1)
        return self._root / "artifacts" / digest[:2] / digest

    def open(self, artifact_id: ArtifactId) -> BinaryIO:
        return self._path(artifact_id).open("rb")

    def verify(self, artifact_id: ArtifactId) -> bool:
        path = self._path(artifact_id)
        return file_sha256(path) == path.name

    def iter_artifact_ids(self) -> Iterator[ArtifactId]:
        artifact_root = self._root / "artifacts"
        if not artifact_root.exists():
            return
        for path in sorted(artifact_root.glob("[0-9a-f][0-9a-f]/*")):
            if path.is_file() and _ARTIFACT_ID.fullmatch(f"art_{path.name}"):
                yield f"art_{path.name}"

    def uri(self, artifact_id: ArtifactId) -> str:
        return self._path(artifact_id).resolve().as_uri()
