from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


class ImmutableStoreConflict(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _same_content(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(_CHUNK_SIZE)
            right_chunk = right_stream.read(_CHUNK_SIZE)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def install_temp_file(
    temp_path: Path,
    final_path: Path,
    *,
    expected_sha256: str | None = None,
) -> bool:
    """Atomically link a same-filesystem temp file without overwriting an object."""

    final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(temp_path, final_path)
        return True
    except FileExistsError:
        if expected_sha256 is not None:
            matches = file_sha256(final_path) == expected_sha256
        else:
            matches = _same_content(temp_path, final_path)
        if not matches:
            raise ImmutableStoreConflict(
                f"immutable path already contains different data: {final_path}"
            ) from None
        return False
    finally:
        temp_path.unlink(missing_ok=True)


def _write_temp_file(final_path: Path, data: bytes) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = tempfile.mkstemp(
        dir=final_path.parent,
        prefix=".tmp-",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(file_descriptor, "wb") as temp_file:
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def write_atomic_bytes(final_path: Path, data: bytes) -> None:
    temp_path = _write_temp_file(final_path, data)
    try:
        os.replace(temp_path, final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def write_immutable_bytes(final_path: Path, data: bytes) -> bool:
    temp_path = _write_temp_file(final_path, data)
    try:
        return install_temp_file(temp_path, final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
