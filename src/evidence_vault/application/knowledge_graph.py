from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from evidence_vault.application.entity_extraction import find_repositories
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.markdown import (
    card_link_ids,
    escape,
    generated_callout,
    generated_frontmatter,
    mermaid_id,
    mermaid_label,
    note_tags,
    wikilink_alias,
)
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.project_fit import (
    ProjectFitIndexEntry,
    ProjectFitIndexStore,
)
from evidence_vault.application.projection_ports import ProjectionStore
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.repo_catalog import parse_repo_ref
from evidence_vault.application.saved_index import saved_texts
from evidence_vault.application.slugs import slugify
from evidence_vault.domain.source import SourcePacket

_MAX_IDEAS_PER_PROJECT = 8


@dataclass(frozen=True)
class _Graph:
    projects: tuple[tuple[str, str], ...]
    ideas: tuple[tuple[str, str, str, str], ...]
    collections: tuple[tuple[str, str], ...]
    repos: tuple[tuple[str, str, str], ...]
    edges: tuple[tuple[str, str, str], ...]


def _load_entries(store: ProjectFitIndexStore) -> dict[str, ProjectFitIndexEntry]:
    try:
        payload = store.load()
    except Exception:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    items = payload.get("items")
    if not isinstance(items, dict):
        return {}
    entries: dict[str, ProjectFitIndexEntry] = {}
    for value in items.values():
        if not isinstance(value, dict):
            continue
        entry = ProjectFitIndexEntry.from_payload(value)
        if entry.source_id:
            entries[entry.source_id] = entry
    return entries


def _idea_title(entry: ProjectFitIndexEntry) -> str:
    if entry.summary:
        first_line = entry.summary.splitlines()[0].strip()
        if first_line:
            return first_line
    return entry.title or entry.source_id


def _repositories(packet: SourcePacket) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for text in saved_texts(packet):
        for url in find_repositories(text):
            found.setdefault(url, None)
    return tuple(found)


def _repo_label(url: str) -> str:
    ref = parse_repo_ref(url)
    return ref.name if ref is not None else url


def _build_graph(
    entries: Mapping[str, ProjectFitIndexEntry],
    packets: Mapping[str, SourcePacket],
) -> _Graph:
    by_project: dict[str, list[ProjectFitIndexEntry]] = defaultdict(list)
    for source_id in sorted(entries):
        entry = entries[source_id]
        for match in entry.matches:
            by_project[match.project_id].append(entry)

    projects: dict[str, str] = {}
    ideas: dict[str, tuple[str, str, str, str]] = {}
    collections: dict[str, str] = {}
    repos: dict[str, tuple[str, str, str]] = {}
    edges: set[tuple[str, str, str]] = set()

    for project_id in sorted(by_project):
        project_node = mermaid_id("project", project_id)
        projects[project_id] = project_node
        seen: set[str] = set()
        selected: list[ProjectFitIndexEntry] = []
        for entry in by_project[project_id]:
            if entry.source_id in seen:
                continue
            seen.add(entry.source_id)
            selected.append(entry)
            if len(selected) >= _MAX_IDEAS_PER_PROJECT:
                break
        for entry in selected:
            idea_node = mermaid_id("idea", entry.source_id)
            ideas[entry.source_id] = (
                entry.source_id,
                entry.capture_id,
                idea_node,
                _idea_title(entry),
            )
            edges.add((project_node, "-->", idea_node))
            packet = packets.get(entry.source_id)
            if packet is None:
                continue
            for collection in packet.collections:
                collection_node = mermaid_id("collection", collection.name)
                collections[collection.name] = collection_node
                edges.add((idea_node, "-->", collection_node))
            for url in _repositories(packet):
                repo_node = mermaid_id("repo", url)
                repos[url] = (url, repo_node, _repo_label(url))
                edges.add((idea_node, "<-->", repo_node))

    return _Graph(
        projects=tuple((key, projects[key]) for key in sorted(projects)),
        ideas=tuple(ideas[key] for key in sorted(ideas)),
        collections=tuple((key, collections[key]) for key in sorted(collections)),
        repos=tuple(repos[key] for key in sorted(repos)),
        edges=tuple(sorted(edges)),
    )


def _render(graph: _Graph, *, generated_prefix: str, generated_at: datetime) -> GeneratedDocument:
    frontmatter = generated_frontmatter(
        {
            "type": "graph",
            "tags": note_tags("graph"),
            "project_count": len(graph.projects),
            "idea_count": len(graph.ideas),
            "collection_count": len(graph.collections),
            "repo_count": len(graph.repos),
            "edge_count": len(graph.edges),
            "generated_at": generated_at.isoformat(),
        },
        schema_version="knowledge-graph/v1",
        generator_version="1",
    )
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        "# Knowledge graph",
        "",
        *generated_callout(
            "Rebuildable from the project-fit index and immutable source packets. "
            "Mermaid labels cannot resolve wikilinks; use the Links section to navigate."
        ),
        "",
        "```mermaid",
        "flowchart LR",
    ]
    lines.extend(
        f'    {node}["{mermaid_label(project_id)}"]' for project_id, node in graph.projects
    )
    lines.extend(f'    {node}["{mermaid_label(label)}"]' for _, _, node, label in graph.ideas)
    lines.extend(f'    {node}["{mermaid_label(name)}"]' for name, node in graph.collections)
    lines.extend(f'    {node}["{mermaid_label(label)}"]' for _, node, label in graph.repos)
    lines.extend(f"    {source} {arrow} {target}" for source, arrow, target in graph.edges)
    lines.extend(["```", "", "## Links", ""])

    lines.extend(["### Projects", ""])
    if graph.projects:
        lines.extend(f"- {escape(project_id)}" for project_id, _ in graph.projects)
    else:
        lines.append("None.")
    lines.append("")

    lines.extend(["### Ideas", ""])
    if graph.ideas:
        lines.extend(
            f"- {card_link_ids(generated_prefix, source_id, capture_id, label)}"
            for source_id, capture_id, _, label in graph.ideas
        )
    else:
        lines.append("None.")
    lines.append("")

    lines.extend(["### Collections", ""])
    if graph.collections:
        lines.extend(
            f"- [[{generated_prefix}/collections/{slugify(name)}|{wikilink_alias(name)}]]"
            for name, _ in graph.collections
        )
    else:
        lines.append("None.")
    lines.append("")

    lines.extend(["### Repositories", ""])
    if graph.repos:
        lines.extend(f"- [{escape(label)}]({url})" for url, _, label in graph.repos)
    else:
        lines.append("None.")
    lines.append("")

    return GeneratedDocument(path="index.md", content="\n".join(lines))


class KnowledgeGraphProjection:
    NAMESPACE = "graph"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        store: ProjectionStore,
        project_fit_index: ProjectFitIndexStore,
        generated_prefix: str = "_generated/evidence-vault",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._store = store
        self._project_fit_index = project_fit_index
        self._generated_prefix = generated_prefix.strip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    def rebuild(self) -> int:
        entries = _load_entries(self._project_fit_index)
        packets = latest_capture_per_source(self._packets.iter_packets())
        graph = _build_graph(entries, packets)
        document = _render(
            graph,
            generated_prefix=self._generated_prefix,
            generated_at=self._clock(),
        )
        return self._store.replace_namespace(self.NAMESPACE, [document])
