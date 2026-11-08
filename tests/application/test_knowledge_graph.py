from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

import yaml
from pydantic import JsonValue

from evidence_vault.application.knowledge_graph import KnowledgeGraphProjection
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRef,
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
_NODE_ID = re.compile(r"^[A-Za-z0-9_]+$")
_NODE_DECLARATION = re.compile(r"^    ([A-Za-z0-9_]+)\[", re.MULTILINE)


class FakePackets:
    def __init__(self, packets: Iterable[SourcePacket]) -> None:
        self._packets = list(packets)

    def iter_packets(self) -> Iterable[SourcePacket]:
        return iter(self._packets)


class FakeIndex:
    def __init__(self, payload: Mapping[str, JsonValue] | None) -> None:
        self._payload = payload

    def load(self) -> Mapping[str, JsonValue] | None:
        return self._payload

    def save(self, payload: Mapping[str, JsonValue]) -> None:
        self._payload = payload


class FakeStore:
    def __init__(self) -> None:
        self.namespace: str | None = None
        self.documents: list[GeneratedDocument] = []

    def replace_namespace(self, namespace: str, documents: Iterable[GeneratedDocument]) -> int:
        self.namespace = namespace
        self.documents = list(documents)
        return len(self.documents)


def _packet(
    external_id: str,
    *,
    collections: tuple[str, ...] = (),
    desc: str = "",
    title: str | None = None,
) -> SourcePacket:
    return SourcePacket.create(
        source=SourceIdentity(
            platform="tiktok",
            kind=SourceKind.VIDEO,
            external_id=external_id,
            canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
        ),
        captured_at=NOW,
        collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
        title=title,
        collections=tuple(CollectionMembership(name=name) for name in collections),
        artifacts=(
            ArtifactRef(
                artifact_id="art_" + "0" * 64,
                role=ArtifactRole.CAPTION_TEXT,
                media_type="text/plain",
                size_bytes=1,
                sha256="0" * 64,
            ),
        ),
        metadata={"saved_index": {"desc": desc}},
    )


def _index_for(pairs: list[tuple[SourcePacket, str]]) -> dict[str, JsonValue]:
    items: dict[str, JsonValue] = {}
    for packet, project_id in pairs:
        items[packet.source_id] = {
            "source_id": packet.source_id,
            "capture_id": packet.capture_id,
            "url": packet.source.canonical_url or "",
            "title": packet.title or "",
            "relevant": True,
            "summary": "",
            "matches": [{"project_id": project_id, "fit": "high", "feature": "f", "why": "w"}],
            "projects_key": "key",
            "updated_at": NOW.isoformat(),
        }
    return {"version": 1, "items": items}


def _render(
    packets: Iterable[SourcePacket],
    index: Mapping[str, JsonValue] | None,
) -> str:
    store = FakeStore()
    count = KnowledgeGraphProjection(
        packets=FakePackets(packets),
        store=store,
        project_fit_index=FakeIndex(index),
        clock=lambda: NOW,
    ).rebuild()
    assert count == 1
    assert store.namespace == "graph"
    assert store.documents[0].path == "index.md"
    return store.documents[0].content


def test_knowledge_graph_renders_one_mermaid_fence_with_edges() -> None:
    packet = _packet(
        "1",
        collections=("Нейронки",),
        desc="repo https://github.com/owner/repo",
        title="Idea",
    )

    content = _render([packet], _index_for([(packet, "midas")]))

    assert content.count("```") == 2
    assert content.count("```mermaid") == 1
    assert "-->" in content
    assert "<-->" in content
    assert "midas" in content
    assert "owner/repo" in content
    assert "Нейронки" in content
    assert "## Links" in content


def test_knowledge_graph_node_ids_are_sanitized() -> None:
    packet = _packet(
        "1",
        collections=("Нейронки",),
        desc="https://github.com/owner/repo",
        title="Idea",
    )

    content = _render([packet], _index_for([(packet, "midas")]))

    node_ids = _NODE_DECLARATION.findall(content)
    assert node_ids
    for node_id in node_ids:
        assert _NODE_ID.fullmatch(node_id)


def test_knowledge_graph_renders_without_index() -> None:
    content = _render([], None)

    assert content.count("```mermaid") == 1
    assert "-->" not in content
    assert "## Links" in content
    assert "None." in content


def test_knowledge_graph_is_deterministic() -> None:
    packet = _packet(
        "1",
        collections=("Нейронки",),
        desc="https://github.com/owner/repo",
        title="Idea",
    )
    index = _index_for([(packet, "midas")])

    assert _render([packet], index) == _render([packet], index)


def test_knowledge_graph_caps_ideas_per_project() -> None:
    packets = [_packet(str(number), title=f"Idea {number}") for number in range(12)]
    index = _index_for([(packet, "midas") for packet in packets])

    content = _render(packets, index)

    idea_ids = re.findall(r"^    (idea_[0-9a-f]+)\[", content, re.MULTILINE)
    assert len(idea_ids) == 8


def test_knowledge_graph_frontmatter_reports_counts() -> None:
    packet = _packet(
        "1",
        collections=("Нейронки",),
        desc="https://github.com/owner/repo",
        title="Idea",
    )

    content = _render([packet], _index_for([(packet, "midas")]))

    properties = yaml.safe_load(content.split("---", 2)[1])
    assert properties["schema_version"] == "knowledge-graph/v1"
    assert properties["type"] == "graph"
    assert properties["tags"] == ["evidence-vault", "evidence-vault/graph"]
    assert properties["project_count"] == 1
    assert properties["idea_count"] == 1
    assert properties["collection_count"] == 1
    assert properties["repo_count"] == 1
    assert properties["edge_count"] == 3
