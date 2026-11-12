from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from sourcefolio.domain.registry import RegistryRoute, RegistrySnapshot


class RegistryRouteStore(Protocol):
    def get(self, route_id: str) -> RegistryRoute | None: ...

    def save(self, route: RegistryRoute) -> bool: ...

    def iter_all(self) -> Iterator[RegistryRoute]: ...


class RegistrySnapshotStore(Protocol):
    def save(self, snapshot: RegistrySnapshot) -> bool: ...

    def load(self, snapshot_id: str) -> RegistrySnapshot | None: ...

    def load_latest(self) -> RegistrySnapshot | None: ...

    def iter_snapshots(self) -> Iterator[RegistrySnapshot]: ...
