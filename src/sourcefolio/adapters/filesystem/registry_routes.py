from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from sourcefolio.adapters.filesystem.atomic import write_immutable_bytes
from sourcefolio.domain.common import canonical_json
from sourcefolio.domain.registry import RegistryRoute


class FileSystemRegistryRouteStore:
    def __init__(self, derived_root: Path) -> None:
        self._root = derived_root / "registry" / "routes"
        self._root.mkdir(parents=True, exist_ok=True)

    def get(self, route_id: str) -> RegistryRoute | None:
        try:
            payload = json.loads((self._root / f"{route_id}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return RegistryRoute.model_validate(payload)
        except ValidationError:
            return None

    def save(self, route: RegistryRoute) -> bool:
        body = canonical_json(route.model_dump(mode="json")) + "\n"
        return write_immutable_bytes(
            self._root / f"{route.route_id}.json",
            body.encode("utf-8"),
        )

    def iter_all(self) -> Iterator[RegistryRoute]:
        if not self._root.is_dir():
            return
        for path in sorted(self._root.glob("rt_*.json")):
            route = self.get(path.stem)
            if route is not None:
                yield route
