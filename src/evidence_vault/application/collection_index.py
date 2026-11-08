from __future__ import annotations

import hashlib
import html
import re
from collections import Counter, defaultdict

import yaml

from evidence_vault.application.entity_extraction import find_hashtags, find_repositories
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.projection_ports import ProjectionStore
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.saved_index import saved_texts
from evidence_vault.domain.source import SourcePacket

_MAX_HASHTAGS = 20

_CYRILLIC = {
    "\u0430": "a",
    "\u0431": "b",
    "\u0432": "v",
    "\u0433": "g",
    "\u0434": "d",
    "\u0435": "e",
    "\u0451": "e",
    "\u0436": "zh",
    "\u0437": "z",
    "\u0438": "i",
    "\u0439": "y",
    "\u043a": "k",
    "\u043b": "l",
    "\u043c": "m",
    "\u043d": "n",
    "\u043e": "o",
    "\u043f": "p",
    "\u0440": "r",
    "\u0441": "s",
    "\u0442": "t",
    "\u0443": "u",
    "\u0444": "f",
    "\u0445": "h",
    "\u0446": "ts",
    "\u0447": "ch",
    "\u0448": "sh",
    "\u0449": "sch",
    "\u044a": "",
    "\u044b": "y",
    "\u044c": "",
    "\u044d": "e",
    "\u044e": "yu",
    "\u044f": "ya",
}


def _md(value: object) -> str:
    text = html.escape(" ".join(str(value).split()), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def _wikilink_alias(value: str) -> str:
    cleaned = " ".join(value.split())
    for character in ("[", "]", "|", "#", "^"):
        cleaned = cleaned.replace(character, " ")
    return " ".join(cleaned.split()) or "source"


def slugify(name: str) -> str:
    lowered = name.strip().lower()
    transliterated = "".join(_CYRILLIC.get(character, character) for character in lowered)
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    if not slug:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        slug = f"collection-{digest}"
    return slug


class CollectionIndexProjection:
    NAMESPACE = "collections"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        store: ProjectionStore,
        generated_prefix: str = "_generated/evidence-vault",
    ) -> None:
        self._packets = packets
        self._store = store
        self._generated_prefix = generated_prefix.strip("/")

    def rebuild(self) -> int:
        grouped: dict[str, list[SourcePacket]] = defaultdict(list)
        for packet in self._packets.iter_packets():
            for collection in packet.collections:
                grouped[collection.name].append(packet)
        slugs = self._slugs(grouped)
        documents = [
            self._render_collection(name, grouped[name], slugs[name])
            for name in sorted(grouped, key=str.casefold)
        ]
        documents.append(self._render_index(grouped, slugs))
        return self._store.replace_namespace(self.NAMESPACE, documents)

    @staticmethod
    def _slugs(grouped: dict[str, list[SourcePacket]]) -> dict[str, str]:
        slugs: dict[str, str] = {}
        used: set[str] = set()
        for name in sorted(grouped, key=str.casefold):
            slug = slugify(name)
            if slug in used:
                digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
                slug = f"{slug}-{digest}"
            used.add(slug)
            slugs[name] = slug
        return slugs

    def _card_link(self, packet: SourcePacket) -> str:
        target = f"{self._generated_prefix}/source-cards/{packet.source_id}/{packet.capture_id}"
        alias = _wikilink_alias(packet.title or packet.source.external_id or packet.source_id)
        return f"[[{target}|{alias}]]"

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

        properties = {
            "schema_version": "collection-index/v1",
            "generated": True,
            "generator": "evidence-vault",
            "generator_version": "1",
            "collection": name,
            "slug": slug,
            "video_count": len(ordered),
        }
        frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {_md(name)}",
            "",
            "> [!note] Generated view",
            "> Rebuildable from immutable source packets. Edit human notes outside this namespace.",
            "",
            f"## Videos ({len(ordered)})",
            "",
        ]
        lines.extend(f"- {self._card_link(packet)}" for packet in ordered)
        lines.extend(["", "## Extracted", ""])
        if repository_counts:
            lines.extend(["### Repositories", "", "| Repository | Videos |", "|---|---:|"])
            lines.extend(
                f"| {_md(repository)} | {count} |"
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
            lines.append(", ".join(f"#{_md(tag)} ({count})" for tag, count in top[:_MAX_HASHTAGS]))
            lines.append("")
        else:
            lines.extend(["No hashtags found.", ""])
        return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))

    def _render_index(
        self,
        grouped: dict[str, list[SourcePacket]],
        slugs: dict[str, str],
    ) -> GeneratedDocument:
        properties = {
            "schema_version": "collection-index/v1",
            "generated": True,
            "generator": "evidence-vault",
            "generator_version": "1",
            "collection_count": len(grouped),
        }
        frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            "# Collections",
            "",
            "> [!note] Generated view",
            "> Rebuildable from immutable source packets. Edit human notes outside this namespace.",
            "",
            "| Collection | Videos | Note |",
            "|---|---:|---|",
        ]
        for name in sorted(grouped, key=str.casefold):
            target = f"{self._generated_prefix}/collections/{slugs[name]}"
            lines.append(
                f"| {_md(name)} | {len(grouped[name])} | [[{target}|{_wikilink_alias(name)}]] |"
            )
        lines.append("")
        return GeneratedDocument(path="index.md", content="\n".join(lines))
