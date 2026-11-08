from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from evidence_vault.application.entity_extraction import find_repositories
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.markdown import (
    card_link,
    escape,
    generated_frontmatter,
    note_tags,
    wikilink_alias,
)
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.project_fit import (
    Project,
    ProjectFitIndexEntry,
    ProjectFitIndexStore,
    index_project_slugs,
    parse_index,
    project_slugs,
)
from evidence_vault.application.projection_ports import ProjectionStore
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.repo_catalog import (
    GITHUB_HOST,
    RepoCacheStore,
    RepoMetadata,
    RepoRef,
    _cache_key,
    parse_repo_ref,
)
from evidence_vault.application.saved_index import saved_texts
from evidence_vault.application.slugs import slugify
from evidence_vault.domain.source import SourcePacket


def _known(value: object) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text if text else "unknown"


def _archived(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def _note_path(ref: RepoRef) -> str:
    _, owner, repository = ref.key.split("/", 2)
    return f"{owner}__{repository}.md"


class RepoNodeProjection:
    NAMESPACE = "entities"

    def __init__(
        self,
        packets: SourcePacketRepository,
        store: ProjectionStore,
        repo_cache: RepoCacheStore,
        project_fit_index: ProjectFitIndexStore,
        generated_prefix: str,
        projects: tuple[Project, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._store = store
        self._repo_cache = repo_cache
        self._project_fit_index = project_fit_index
        self._generated_prefix = generated_prefix.strip("/")
        self._project_slugs = project_slugs(projects)
        self._clock = clock

    def rebuild(self) -> int:
        payload = self._project_fit_index.load()
        fit = parse_index(payload)
        slugs = index_project_slugs(payload)
        documents = [self._render(ref, packets, fit, slugs) for ref, packets in self._collect()]
        return self._store.replace_namespace(self.NAMESPACE, documents)

    def _collect(self) -> list[tuple[RepoRef, tuple[SourcePacket, ...]]]:
        latest = latest_capture_per_source(self._packets.iter_packets())
        refs: dict[str, RepoRef] = {}
        mentions: dict[str, list[SourcePacket]] = {}
        for packet in sorted(latest.values(), key=lambda item: (item.source_id, item.capture_id)):
            seen: dict[str, RepoRef] = {}
            for text in saved_texts(packet):
                for url in find_repositories(text):
                    ref = parse_repo_ref(url)
                    if ref is not None and ref.host == GITHUB_HOST:
                        seen.setdefault(ref.key, ref)
            for key, ref in seen.items():
                refs.setdefault(key, ref)
                mentions.setdefault(key, []).append(packet)
        return [(refs[key], tuple(mentions[key])) for key in sorted(refs)]

    def _metadata(self, ref: RepoRef) -> RepoMetadata | None:
        try:
            payload = self._repo_cache.load(_cache_key(ref))
            if payload is None:
                return None
            return RepoMetadata.from_payload(payload)
        except Exception:
            return None

    @staticmethod
    def _project_ids(
        packets: tuple[SourcePacket, ...],
        fit: Mapping[str, ProjectFitIndexEntry],
    ) -> list[str]:
        ids: set[str] = set()
        for packet in packets:
            entry = fit.get(packet.source_id)
            if entry is None:
                continue
            ids.update(match.project_id for match in entry.matches if match.project_id)
        return sorted(ids)

    def _project_link(self, project_id: str, slugs: Mapping[str, str]) -> str:
        slug = slugs.get(project_id) or self._project_slugs.get(project_id) or slugify(project_id)
        target = f"{self._generated_prefix}/projects/{slug}"
        return f"[[{target}|{wikilink_alias(project_id)}]]"

    def _render(
        self,
        ref: RepoRef,
        packets: tuple[SourcePacket, ...],
        fit: Mapping[str, ProjectFitIndexEntry],
        slugs: Mapping[str, str],
    ) -> GeneratedDocument:
        metadata = self._metadata(ref)
        project_ids = self._project_ids(packets, fit)
        full_name = metadata.full_name if metadata is not None else ref.name
        url = metadata.html_url if metadata is not None and metadata.html_url else ref.url
        stars = metadata.stars if metadata is not None else None
        language = metadata.language if metadata is not None else ""
        license_name = metadata.license if metadata is not None else ""
        archived = metadata.archived if metadata is not None else None
        frontmatter = generated_frontmatter(
            {
                "type": "repo",
                "tags": note_tags("repo"),
                "full_name": full_name,
                "url": url,
                "stars": stars,
                "language": language,
                "license": license_name,
                "archived": archived,
                "source_count": len(packets),
                "project_ids": project_ids,
                "provenance": "external_observation",
            },
            schema_version="repo-node/v1",
            generator_version="1",
        )
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {escape(full_name)}",
            "",
            "> [!note] External observation",
            "> Repository facts come from the GitHub API cache under data/derived/repos. "
            "They are external_observation, not creator_claim and not agent inference. "
            "Rebuildable from the cache; verify before relying on them.",
            "",
            "## Metadata",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Full name | {escape(full_name, table=True)} |",
            f"| URL | {escape(url, table=True)} |",
            f"| Stars | {escape(_known(stars), table=True)} |",
            f"| Language | {escape(_known(language), table=True)} |",
            f"| License | {escape(_known(license_name), table=True)} |",
            f"| Archived | {_archived(archived)} |",
            f"| Sources | {len(packets)} |",
            "",
            f"## Sources ({len(packets)})",
            "",
        ]
        lines.extend(f"- {card_link(self._generated_prefix, packet)}" for packet in packets)
        lines.append("")
        lines.extend([f"## Projects ({len(project_ids)})", ""])
        if project_ids:
            lines.extend(f"- {self._project_link(project_id, slugs)}" for project_id in project_ids)
        else:
            lines.append("None.")
        lines.append("")
        return GeneratedDocument(path=_note_path(ref), content="\n".join(lines))
