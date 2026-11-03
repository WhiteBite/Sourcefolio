from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from evidence_vault.application.projection_types import GeneratedDocument

_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class FileSystemProjectionStore:
    """A dedicated generated root; human-owned notes must live outside it."""

    def __init__(self, generated_root: Path) -> None:
        self._root = generated_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _relative_path(value: str) -> Path:
        if not value or "\\" in value:
            raise ValueError("generated path must be a non-empty POSIX relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("generated path escapes its namespace")
        return Path(*path.parts)

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def replace_namespace(
        self,
        namespace: str,
        documents: Iterable[GeneratedDocument],
    ) -> int:
        if _NAMESPACE.fullmatch(namespace) is None:
            raise ValueError("invalid generated namespace")
        final_root = self._root / namespace
        backup_root = self._root / f".{namespace}.backup"
        staging_root = Path(tempfile.mkdtemp(dir=self._root, prefix=f".{namespace}.staging-"))
        paths: list[str] = []
        try:
            for document in documents:
                relative = self._relative_path(document.path)
                self._write_atomic(staging_root / relative, document.content)
                paths.append(document.path)
            manifest = {
                "schema_version": "generated-projection/v1",
                "namespace": namespace,
                "documents": sorted(paths),
            }
            self._write_atomic(
                staging_root / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )

            if backup_root.exists():
                shutil.rmtree(backup_root)
            if final_root.exists():
                final_root.rename(backup_root)
            try:
                staging_root.rename(final_root)
            except Exception:
                if backup_root.exists() and not final_root.exists():
                    backup_root.rename(final_root)
                raise
            if backup_root.exists():
                shutil.rmtree(backup_root)
            return len(paths)
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)
