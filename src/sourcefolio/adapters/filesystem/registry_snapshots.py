from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from sourcefolio.adapters.filesystem.atomic import write_atomic_bytes, write_immutable_bytes
from sourcefolio.domain.common import canonical_json
from sourcefolio.domain.registry import RegistrySnapshot


class FileSystemRegistrySnapshotStore:
    def __init__(self, derived_root: Path) -> None:
        self._root = derived_root / "registry"
        self._snapshots = self._root / "snapshots"
        self._snapshots.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: RegistrySnapshot) -> bool:
        body = canonical_json(snapshot.model_dump(mode="json")) + "\n"
        created = write_immutable_bytes(
            self._snapshots / f"{snapshot.snapshot_id}.json",
            body.encode("utf-8"),
        )
        latest = canonical_json({"snapshot_id": snapshot.snapshot_id}) + "\n"
        write_atomic_bytes(self._root / "latest.json", latest.encode("utf-8"))
        return created

    def load(self, snapshot_id: str) -> RegistrySnapshot | None:
        try:
            payload = json.loads(
                (self._snapshots / f"{snapshot_id}.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("snapshot_id") != snapshot_id:
            return None
        try:
            return RegistrySnapshot.model_validate(payload)
        except ValidationError:
            return None

    def load_latest(self) -> RegistrySnapshot | None:
        try:
            payload = json.loads((self._root / "latest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        snapshot_id = payload.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            return None
        return self.load(snapshot_id)

    def iter_snapshots(self) -> Iterator[RegistrySnapshot]:
        if not self._snapshots.is_dir():
            return
        for path in sorted(self._snapshots.glob("snap_*.json")):
            snapshot = self.load(path.stem)
            if snapshot is not None:
                yield snapshot
