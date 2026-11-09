from __future__ import annotations

from collections import Counter, defaultdict

from sourcefolio.application.entity_extraction import find_hashtags, find_repositories
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.markdown import (
    card_link,
    escape,
    generated_callout,
    generated_frontmatter,
    wikilink_alias,
)
from sourcefolio.application.ports import SourcePacketRepository
from sourcefolio.application.projection_ports import ProjectionStore
from sourcefolio.application.projection_types import GeneratedDocument
from sourcefolio.application.saved_index import saved_texts
from sourcefolio.application.slugs import unique_slugs
from sourcefolio.domain.source import SourcePacket

_MAX_HASHTAGS = 20


class CollectionIndexProjection:
    NAMESPACE = "collections"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        store: ProjectionStore,
        generated_prefix: str = "_generated/sourcefolio",
    ) -> None:
        self._packets = packets
        self._store = store
        self._generated_prefix = generated_prefix.strip("/")

    def rebuild(self) -> int:
        grouped: dict[str, list[SourcePacket]] = defaultdict(list)
        for packet in latest_capture_per_source(self._packets.iter_packets()).values():
            for collection in packet.collections:
                grouped[collection.name].append(packet)
        slugs = unique_slugs(grouped)
        documents = [
            self._render_collection(name, grouped[name], slugs[name])
            for name in sorted(grouped, key=str.casefold)
        ]
        documents.append(self._render_index(grouped, slugs))
        return self._store.replace_namespace(self.NAMESPACE, documents)

    def _render_collection(
        self,
        name: str,
        packets: list[SourcePacket],
        slug: str,
    ) -> GeneratedDocument:
        ordered = sorted(
            packets,
            key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id),
        )
        repository_counts: Counter[str] = Counter()
        hashtag_counts: Counter[str] = Counter()
        for packet in ordered:
            texts = saved_texts(packet)
            repositories: set[str] = set()
            for text in texts:
                repositories.update(find_repositories(text))
                for hashtag in find_hashtags(text):
                    hashtag_counts[hashtag] += 1
            for repository in repositories:
                repository_counts[repository] += 1

        frontmatter = generated_frontmatter(
            {
                "collection": name,
                "slug": slug,
                "video_count": len(ordered),
            },
            schema_version="collection-index/v1",
            generator_version="1",
        )
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {escape(name, table=True)}",
            "",
            *generated_callout(
                "Rebuildable from immutable source packets. "
                "Edit human notes outside this namespace."
            ),
            "",
            f"## Videos ({len(ordered)})",
            "",
        ]
        lines.extend(f"- {card_link(self._generated_prefix, packet)}" for packet in ordered)
        lines.extend(["", "## Extracted", ""])
        if repository_counts:
            lines.extend(["### Repositories", "", "| Repository | Videos |", "|---|---:|"])
            lines.extend(
                f"| {escape(repository, table=True)} | {count} |"
                for repository, count in sorted(
                    repository_counts.items(), key=lambda item: (-item[1], item[0])
                )
            )
            lines.append("")
        else:
            lines.extend(["No repository links found.", ""])
        if hashtag_counts:
            lines.extend(["### Hashtags", ""])
            top = sorted(hashtag_counts.items(), key=lambda item: (-item[1], item[0]))
            lines.append(
                ", ".join(
                    f"#{escape(tag, table=True)} ({count})" for tag, count in top[:_MAX_HASHTAGS]
                )
            )
            lines.append("")
        else:
            lines.extend(["No hashtags found.", ""])
        return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))

    def _render_index(
        self,
        grouped: dict[str, list[SourcePacket]],
        slugs: dict[str, str],
    ) -> GeneratedDocument:
        frontmatter = generated_frontmatter(
            {"collection_count": len(grouped)},
            schema_version="collection-index/v1",
            generator_version="1",
        )
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            "# Collections",
            "",
            *generated_callout(
                "Rebuildable from immutable source packets. "
                "Edit human notes outside this namespace."
            ),
            "",
            "| Collection | Videos | Note |",
            "|---|---:|---|",
        ]
        for name in sorted(grouped, key=str.casefold):
            target = f"{self._generated_prefix}/collections/{slugs[name]}"
            lines.append(
                f"| {escape(name, table=True)} | {len(grouped[name])} "
                f"| [[{target}|{wikilink_alias(name)}]] |"
            )
        lines.append("")
        return GeneratedDocument(path="index.md", content="\n".join(lines))
