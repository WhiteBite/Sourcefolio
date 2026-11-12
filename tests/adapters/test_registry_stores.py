from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sourcefolio.adapters.filesystem.registry_routes import FileSystemRegistryRouteStore
from sourcefolio.adapters.filesystem.registry_snapshots import FileSystemRegistrySnapshotStore
from sourcefolio.domain.common import ActorKind, Provenance, content_id
from sourcefolio.domain.registry import (
    AnchorField,
    Candidate,
    CandidateKind,
    CandidateMention,
    FitLevel,
    MentionAnchor,
    RegistryRoute,
    RegistrySnapshot,
    RouteVerdict,
    SnapshotProject,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SOURCE_ID = "src_" + "1" * 64
CAPTURE_ID = "cap_" + "2" * 64
PROJECT_ID = "sourcefolio"
CANDIDATE_KEY = "owner/repository"
PROMPT_FINGERPRINT = "0123456789abcdef"
MISSING_ROUTE_ID = "rt_" + "0" * 64
MISSING_SNAPSHOT_ID = "snap_" + "0" * 64


def provenance() -> Provenance:
    return Provenance(actor_kind=ActorKind.AGENT, actor_id="agent", method="registry-test")


def mention() -> CandidateMention:
    payload: dict[str, object] = {
        "candidate_key": CANDIDATE_KEY,
        "kind": CandidateKind.TOOL,
        "title": "Example tool",
        "fit": FitLevel.HIGH,
        "anchor": MentionAnchor(
            field=AnchorField.DESCRIPTION,
            quote="useful example tool",
            start_char=0,
            end_char=19,
        ).model_dump(mode="json"),
    }
    return CandidateMention(mention_id=content_id("men", payload), **payload)


def make_route(*, model: str = "test-model", source_id: str = SOURCE_ID) -> RegistryRoute:
    identity: dict[str, str] = {
        "source_id": source_id,
        "capture_id": CAPTURE_ID,
        "project_id": PROJECT_ID,
        "model": model,
        "prompt_fingerprint": PROMPT_FINGERPRINT,
    }
    return RegistryRoute(
        route_id=content_id("rt", identity),
        **identity,
        verdict=RouteVerdict.CANDIDATE,
        mentions=(mention(),),
        captured_at=NOW,
        provenance=provenance(),
        created_at=NOW,
    )


def make_candidate() -> Candidate:
    payload = {"project_id": PROJECT_ID, "key": CANDIDATE_KEY}
    return Candidate(
        candidate_id=content_id("cand", payload),
        project_id=PROJECT_ID,
        key=CANDIDATE_KEY,
        kind=CandidateKind.TOOL,
        title="Example tool",
        fit=FitLevel.HIGH,
        mention_ids=(mention().mention_id,),
        source_ids=(SOURCE_ID,),
        mention_count=1,
    )


def snapshot_payload(
    projects: tuple[SnapshotProject, ...],
    candidates: tuple[Candidate, ...],
    route_count: int,
) -> dict[str, object]:
    return {
        "schema_version": "registry-snapshot/v1",
        "projects": [project.model_dump(mode="json") for project in projects],
        "candidates": [value.model_dump(mode="json") for value in candidates],
        "route_count": route_count,
        "insufficient_signal_routes": 0,
        "dropped_claims": 0,
    }


def make_snapshot(*, route_count: int = 1) -> RegistrySnapshot:
    projects = (
        SnapshotProject(
            project_id=PROJECT_ID,
            name="Sourcefolio",
            candidate_count=1,
            accepted=0,
            rejected=0,
            stale=0,
        ),
    )
    candidates = (make_candidate(),)
    return RegistrySnapshot(
        snapshot_id=content_id("snap", snapshot_payload(projects, candidates, route_count)),
        generated_at=NOW,
        projects=projects,
        candidates=candidates,
        route_count=route_count,
        insufficient_signal_routes=0,
        dropped_claims=0,
    )


def route_path(derived_root: Path, route: RegistryRoute) -> Path:
    return derived_root / "registry" / "routes" / f"{route.route_id}.json"


def snapshot_path(derived_root: Path, snapshot: RegistrySnapshot) -> Path:
    return derived_root / "registry" / "snapshots" / f"{snapshot.snapshot_id}.json"


def test_route_first_save_and_identical_resave(tmp_path: Path) -> None:
    store = FileSystemRegistryRouteStore(tmp_path)
    route = make_route()

    assert store.save(route) is True
    original = route_path(tmp_path, route).read_bytes()

    assert store.save(route) is False
    assert route_path(tmp_path, route).read_bytes() == original
    assert store.get(route.route_id) == route


def test_route_get_returns_none_for_invalid_payloads(tmp_path: Path) -> None:
    store = FileSystemRegistryRouteStore(tmp_path)
    route = make_route()
    store.save(route)
    path = route_path(tmp_path, route)

    path.write_text("{not json", encoding="utf-8")
    assert store.get(route.route_id) is None

    path.write_text("[]", encoding="utf-8")
    assert store.get(route.route_id) is None

    path.write_text(json.dumps({"route_id": MISSING_ROUTE_ID}), encoding="utf-8")
    assert store.get(route.route_id) is None


def test_route_iter_all_is_sorted(tmp_path: Path) -> None:
    store = FileSystemRegistryRouteStore(tmp_path)
    routes = [make_route(model=f"model-{index}") for index in range(3)]
    for route in routes:
        store.save(route)

    assert [route.route_id for route in store.iter_all()] == sorted(
        route.route_id for route in routes
    )


def test_snapshot_first_save_and_identical_resave(tmp_path: Path) -> None:
    store = FileSystemRegistrySnapshotStore(tmp_path)
    snapshot = make_snapshot()

    assert store.save(snapshot) is True
    original = snapshot_path(tmp_path, snapshot).read_bytes()

    assert store.save(snapshot) is False
    assert snapshot_path(tmp_path, snapshot).read_bytes() == original
    assert store.load(snapshot.snapshot_id) == snapshot


def test_snapshot_load_returns_none_for_invalid_payloads(tmp_path: Path) -> None:
    store = FileSystemRegistrySnapshotStore(tmp_path)
    snapshot = make_snapshot()
    store.save(snapshot)
    path = snapshot_path(tmp_path, snapshot)

    path.write_text("{not json", encoding="utf-8")
    assert store.load(snapshot.snapshot_id) is None

    path.write_text("[]", encoding="utf-8")
    assert store.load(snapshot.snapshot_id) is None

    path.write_text(json.dumps({"snapshot_id": MISSING_SNAPSHOT_ID}), encoding="utf-8")
    assert store.load(snapshot.snapshot_id) is None


def test_snapshot_load_rejects_mismatched_id(tmp_path: Path) -> None:
    store = FileSystemRegistrySnapshotStore(tmp_path)
    snapshot = make_snapshot()
    store.save(snapshot)
    mismatched = tmp_path / "registry" / "snapshots" / f"{MISSING_SNAPSHOT_ID}.json"
    mismatched.write_bytes(snapshot_path(tmp_path, snapshot).read_bytes())

    assert store.load(MISSING_SNAPSHOT_ID) is None


def test_latest_follows_newest_snapshot(tmp_path: Path) -> None:
    store = FileSystemRegistrySnapshotStore(tmp_path)
    first = make_snapshot(route_count=1)
    second = make_snapshot(route_count=2)

    store.save(first)
    assert store.load_latest() == first

    store.save(second)
    assert store.load_latest() == second

    latest = json.loads((tmp_path / "registry" / "latest.json").read_text(encoding="utf-8"))
    assert latest == {"snapshot_id": second.snapshot_id}


def test_iter_snapshots_is_sorted(tmp_path: Path) -> None:
    store = FileSystemRegistrySnapshotStore(tmp_path)
    snapshots = [make_snapshot(route_count=count) for count in (2, 1)]

    for snapshot in snapshots:
        store.save(snapshot)

    assert [snapshot.snapshot_id for snapshot in store.iter_snapshots()] == sorted(
        snapshot.snapshot_id for snapshot in snapshots
    )


def test_missing_directories_return_empty_results(tmp_path: Path) -> None:
    route_store = FileSystemRegistryRouteStore(tmp_path)
    snapshot_store = FileSystemRegistrySnapshotStore(tmp_path)
    (tmp_path / "registry" / "routes").rmdir()
    (tmp_path / "registry" / "snapshots").rmdir()

    assert list(route_store.iter_all()) == []
    assert route_store.get(MISSING_ROUTE_ID) is None
    assert list(snapshot_store.iter_snapshots()) == []
    assert snapshot_store.load(MISSING_SNAPSHOT_ID) is None
    assert snapshot_store.load_latest() is None
