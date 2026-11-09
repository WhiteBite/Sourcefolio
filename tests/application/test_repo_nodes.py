from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from pydantic import JsonValue

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemProjectFitIndex,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.llm_cache import FileSystemLlmCache
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.project_fit import (
    Project,
    ProjectFitIndexStore,
    ProjectFitQuery,
    ProjectFitRunner,
)
from sourcefolio.application.projection_types import GeneratedDocument
from sourcefolio.application.repo_catalog import (
    RepoCatalogRunner,
    _cache_key,
    parse_repo_ref,
)
from sourcefolio.application.repo_nodes import RepoNodeProjection
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PREFIX = "_generated/sourcefolio"


class FakeStore:
    def __init__(self) -> None:
        self.namespace: str | None = None
        self.documents: list[GeneratedDocument] = []

    def replace_namespace(
        self,
        namespace: str,
        documents: Iterable[GeneratedDocument],
    ) -> int:
        self.namespace = namespace
        self.documents = list(documents)
        return len(self.documents)


class FakeFitIndex:
    def __init__(self, payload: Mapping[str, JsonValue] | None = None) -> None:
        self._payload = payload

    def load(self) -> Mapping[str, JsonValue] | None:
        return self._payload

    def save(self, payload: Mapping[str, JsonValue]) -> None:
        self._payload = payload


def add_packet(
    root: Path,
    external_id: str,
    *,
    desc: str = "текст",
    captured_at: datetime = NOW,
) -> SourcePacket:
    return (
        ArchiveCapture(
            artifacts=FileSystemArtifactStore(root),
            packets=FileSystemSourcePacketRepository(root),
        )
        .handle(
            ArchiveCaptureCommand(
                source=SourceIdentity(
                    platform="tiktok",
                    kind=SourceKind.VIDEO,
                    external_id=external_id,
                    canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
                ),
                captured_at=captured_at,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                collections=(CollectionMembership(name="Нейронки"),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption"),
                    ),
                ),
                metadata={"saved_index": {"author": "author", "desc": desc}},
            )
        )
        .packet
    )


def write_cache(root: Path, name: str, **extra: JsonValue) -> None:
    ref = parse_repo_ref(f"https://github.com/{name}")
    assert ref is not None
    payload: dict[str, JsonValue] = {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": "",
        "stars": 0,
        "language": "",
        "license": "",
        "archived": False,
        "pushed_at": "",
        "topics": [],
    }
    payload.update(extra)
    FileSystemLlmCache(root / "derived", namespace=RepoCatalogRunner.NAMESPACE).save(
        _cache_key(ref), payload
    )


def projection(
    root: Path,
    store: FakeStore,
    *,
    fit: ProjectFitIndexStore | None = None,
    projects: tuple[Project, ...] = (),
) -> RepoNodeProjection:
    return RepoNodeProjection(
        packets=FileSystemSourcePacketRepository(root),
        store=store,
        repo_cache=FileSystemLlmCache(root / "derived", namespace=RepoCatalogRunner.NAMESPACE),
        project_fit_index=fit or FakeFitIndex(),
        generated_prefix=PREFIX,
        clock=lambda: NOW,
        projects=projects,
    )


class FakeChatClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(self._payload)


def project_link_target(content: str, alias: str) -> str:
    marker = f"|{alias}]]"
    for line in content.splitlines():
        if line.startswith("- [[") and "/projects/" in line and marker in line:
            return line[len("- [[") : line.index("|")].rsplit("/", 1)[-1]
    raise AssertionError(f"no project link for {alias}")


def fit_payload(packet: SourcePacket, project_id: str) -> dict[str, JsonValue]:
    return {
        "version": 1,
        "items": {
            packet.source_id: {
                "source_id": packet.source_id,
                "capture_id": packet.capture_id,
                "url": "",
                "title": "",
                "relevant": True,
                "summary": "",
                "matches": [{"project_id": project_id, "fit": "high", "feature": "x", "why": "y"}],
                "projects_key": "k",
                "updated_at": "",
            }
        },
    }


def test_repo_nodes_one_note_per_repo_lists_all_sources(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="see https://github.com/owner/repo")
    add_packet(tmp_path, "two", desc="also https://github.com/OWNER/repo")
    write_cache(tmp_path, "owner/repo", stars=42, language="Python")
    store = FakeStore()

    count = projection(tmp_path, store).rebuild()

    assert count == 1
    assert store.namespace == "entities"
    assert [doc.path for doc in store.documents] == ["owner__repo.md"]
    content = store.documents[0].content
    assert content.count("[[_generated/sourcefolio/source-cards/") == 2
    assert "| 42 |" in content
    assert "| Python |" in content
    assert "type: repo" in content
    assert "provenance: external_observation" in content
    assert "schema_version: repo-node/v1" in content


def test_repo_nodes_links_matched_project(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    write_cache(tmp_path, "owner/repo")
    store = FakeStore()
    projects = (Project(id="midas", name="MidasAI", description="trading bot"),)

    projection(
        tmp_path,
        store,
        fit=FakeFitIndex(fit_payload(packet, "midas")),
        projects=projects,
    ).rebuild()

    content = store.documents[0].content
    assert "[[_generated/sourcefolio/projects/midasai|midas]]" in content
    assert "## Projects (1)" in content
    assert "project_ids:\n- midas" in content


def test_repo_nodes_project_link_matches_written_brief_on_slug_collision(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    write_cache(tmp_path, "owner/repo")
    projects = (
        Project(id="midas", name="MidasAI", description="a"),
        Project(id="midas2", name="midasai", description="b"),
    )
    index = FileSystemProjectFitIndex(tmp_path / "derived")
    report = ProjectFitRunner(
        packets=FileSystemSourcePacketRepository(tmp_path),
        client=FakeChatClient(
            {
                "relevant": True,
                "summary": "",
                "matches": [{"project_id": "midas2", "fit": "high", "feature": "x", "why": "y"}],
            }
        ),
        cache=FileSystemLlmCache(tmp_path / "derived", namespace=ProjectFitRunner.CACHE_NAMESPACE),
        index=index,
        model="test-model",
        projects=projects,
        clock=lambda: NOW,
        sleep=lambda _: None,
    ).run(ProjectFitQuery())
    written = {note.path for note in report.notes}
    assert "midasai.md" in written
    assert "midasai-midas2.md" in written
    brief = next(note for note in report.notes if note.path == "midasai-midas2.md")

    store = FakeStore()
    projection(tmp_path, store, fit=index, projects=projects).rebuild()

    content = store.documents[0].content
    assert "[[_generated/sourcefolio/projects/midasai-midas2|midas2]]" in content
    assert project_link_target(content, "midas2") == brief.path.removesuffix(".md")


def test_repo_nodes_project_link_uses_persisted_slug_map(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    write_cache(tmp_path, "owner/repo")
    store = FakeStore()
    payload = fit_payload(packet, "pi")
    payload["project_slugs"] = {"pi": "proyecto-ilimitado"}

    projection(tmp_path, store, fit=FakeFitIndex(payload)).rebuild()

    content = store.documents[0].content
    assert "[[_generated/sourcefolio/projects/proyecto-ilimitado|pi]]" in content


def test_repo_nodes_project_link_matches_written_brief_without_projects(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    write_cache(tmp_path, "owner/repo")
    projects = (Project(id="midas-ai", name="Midas AI", description="a"),)
    index = FileSystemProjectFitIndex(tmp_path / "derived")
    report = ProjectFitRunner(
        packets=FileSystemSourcePacketRepository(tmp_path),
        client=FakeChatClient(
            {
                "relevant": True,
                "summary": "",
                "matches": [{"project_id": "midas-ai", "fit": "high", "feature": "x", "why": "y"}],
            }
        ),
        cache=FileSystemLlmCache(tmp_path / "derived", namespace=ProjectFitRunner.CACHE_NAMESPACE),
        index=index,
        model="test-model",
        projects=projects,
        clock=lambda: NOW,
        sleep=lambda _: None,
    ).run(ProjectFitQuery())
    brief = next(note for note in report.notes if note.path == "midas-ai.md")

    store = FakeStore()
    projection(tmp_path, store, fit=index).rebuild()

    content = store.documents[0].content
    assert project_link_target(content, "midas-ai") == brief.path.removesuffix(".md")


def test_repo_nodes_without_fit_index_has_empty_projects(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    write_cache(tmp_path, "owner/repo")
    store = FakeStore()

    projection(tmp_path, store).rebuild()

    content = store.documents[0].content
    assert "project_ids: []" in content
    assert "## Projects (0)" in content
    assert "None." in content


def test_repo_nodes_missing_cache_marks_unknown(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    store = FakeStore()

    count = projection(tmp_path, store).rebuild()

    assert count == 1
    content = store.documents[0].content
    assert "| Stars | unknown |" in content
    assert "| Language | unknown |" in content
    assert "| License | unknown |" in content
    assert "| Archived | unknown |" in content
    assert "full_name: owner/repo" in content
    assert "url: https://github.com/owner/repo" in content


def test_repo_nodes_ignores_non_github(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://gitlab.com/group/project")
    store = FakeStore()

    count = projection(tmp_path, store).rebuild()

    assert count == 0
    assert store.documents == []


def test_repo_nodes_deterministic(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    add_packet(tmp_path, "two", desc="https://github.com/owner/other")
    write_cache(tmp_path, "owner/repo", stars=1)
    first = FakeStore()
    second = FakeStore()

    projection(tmp_path, first).rebuild()
    projection(tmp_path, second).rebuild()

    assert [doc.path for doc in first.documents] == [doc.path for doc in second.documents]
    assert [doc.content for doc in first.documents] == [doc.content for doc in second.documents]
