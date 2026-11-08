from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import JsonValue

from evidence_vault.application.entity_extraction import find_repositories
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.markdown import card_link, escape, generated_frontmatter
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.saved_index import saved_texts
from evidence_vault.application.slugs import slugify
from evidence_vault.domain.source import SourcePacket

GITHUB_HOST = "github.com"

_REPO_URL = re.compile(
    r"(?:https?://)?(?:www\.)?([A-Za-z0-9.-]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _as_str_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for text in (_as_str(item) for item in value) if text)


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    repository: str

    @property
    def key(self) -> str:
        return f"{self.host}/{self.owner}/{self.repository}".casefold()

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.repository}"

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repository}"


def parse_repo_ref(value: str) -> RepoRef | None:
    match = _REPO_URL.fullmatch(value.strip())
    if match is None:
        return None
    host = match.group(1).lower().removeprefix("www.")
    owner = match.group(2).rstrip(".-")
    repository = match.group(3).rstrip(".-")
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not host or not owner or not repository:
        return None
    return RepoRef(host=host, owner=owner, repository=repository)


@dataclass(frozen=True)
class RepoMetadata:
    full_name: str
    html_url: str
    description: str
    stars: int | None
    language: str
    license: str
    archived: bool
    pushed_at: str
    topics: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, JsonValue]) -> RepoMetadata:
        full_name = _as_str(payload.get("full_name"))
        if not full_name:
            raise ValueError("repository metadata has no full_name")
        return cls(
            full_name=full_name,
            html_url=_as_str(payload.get("html_url")),
            description=_as_str(payload.get("description")),
            stars=_as_int(payload.get("stars")),
            language=_as_str(payload.get("language")),
            license=_as_str(payload.get("license")),
            archived=payload.get("archived") is True,
            pushed_at=_as_str(payload.get("pushed_at")),
            topics=_as_str_list(payload.get("topics")),
        )

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "full_name": self.full_name,
            "html_url": self.html_url,
            "description": self.description,
            "stars": self.stars,
            "language": self.language,
            "license": self.license,
            "archived": self.archived,
            "pushed_at": self.pushed_at,
            "topics": list(self.topics),
        }


class RepoMetadataFetcher(Protocol):
    def fetch(self, owner: str, repository: str) -> Mapping[str, JsonValue]:
        """Return the canonical metadata payload for one repository."""
        ...


class RepoCacheStore(Protocol):
    def load(self, key: str) -> Mapping[str, JsonValue] | None:
        """Return the cached metadata payload for a key, or None when absent."""
        ...

    def save(self, key: str, payload: Mapping[str, JsonValue]) -> None:
        """Persist the metadata payload for a key, replacing any previous entry."""
        ...


@dataclass(frozen=True)
class RepoCatalogQuery:
    collection: str
    limit: int | None = None
    refresh: bool = False
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.collection.strip():
            raise ValueError("collection name is required")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")


@dataclass(frozen=True)
class RepoCatalogItemResult:
    repository: str
    url: str
    status: Literal["fetched", "skipped_cached", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class RepoCatalogReport:
    collection: str
    repos_found: int
    fetched: int
    skipped_cached: int
    failed: int
    skipped_non_github: int
    dry_run: bool
    note: GeneratedDocument | None
    results: tuple[RepoCatalogItemResult, ...]


def _cache_key(ref: RepoRef) -> str:
    return hashlib.sha256(ref.name.casefold().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class _RepoRow:
    ref: RepoRef
    metadata: RepoMetadata
    packets: tuple[SourcePacket, ...]


@dataclass(frozen=True)
class _Collected:
    github: Mapping[str, RepoRef]
    mentions: Mapping[str, tuple[SourcePacket, ...]]
    other: tuple[RepoRef, ...]


def _collect(selected: list[SourcePacket]) -> _Collected:
    github: dict[str, RepoRef] = {}
    mentions: dict[str, list[SourcePacket]] = {}
    other: dict[str, RepoRef] = {}
    for packet in selected:
        seen: dict[str, RepoRef] = {}
        for text in saved_texts(packet):
            for url in find_repositories(text):
                ref = parse_repo_ref(url)
                if ref is not None:
                    seen.setdefault(ref.key, ref)
        for key, ref in seen.items():
            if ref.host == GITHUB_HOST:
                github.setdefault(key, ref)
                mentions.setdefault(key, []).append(packet)
            else:
                other.setdefault(key, ref)
    return _Collected(
        github=github,
        mentions={key: tuple(value) for key, value in mentions.items()},
        other=tuple(other[key] for key in sorted(other)),
    )


def _render_row(generated_prefix: str, row: _RepoRow) -> str:
    metadata = row.metadata
    name = metadata.full_name or row.ref.name
    url = metadata.html_url or row.ref.url
    stars = "" if metadata.stars is None else str(metadata.stars)
    pushed = metadata.pushed_at.split("T", 1)[0]
    sources = ", ".join(card_link(generated_prefix, packet) for packet in row.packets)
    return (
        f"| {escape(name, table=True)} "
        f"| {escape(url, table=True)} "
        f"| {escape(metadata.description, table=True)} "
        f"| {stars} "
        f"| {escape(metadata.language, table=True)} "
        f"| {escape(metadata.license, table=True)} "
        f"| {'yes' if metadata.archived else 'no'} "
        f"| {escape(pushed, table=True)} "
        f"| {len(row.packets)} "
        f"| {sources} |"
    )


def _render_note(
    *,
    collection: str,
    slug: str,
    generated_at: datetime,
    generated_prefix: str,
    video_count: int,
    rows: list[_RepoRow],
    failures: list[tuple[RepoRef, str]],
    skipped: tuple[RepoRef, ...],
) -> GeneratedDocument:
    frontmatter = generated_frontmatter(
        {
            "collection": collection,
            "slug": slug,
            "repo_count": len(rows),
            "video_count": video_count,
            "skipped_non_github": len(skipped),
            "generated_at": generated_at.isoformat(),
            "provenance": "external_observation",
        },
        schema_version="repo-catalog/v1",
        generator_version="1",
    )
    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {escape(collection, table=True)} — repository catalog",
        "",
        "> [!note] External observation",
        "> Repository facts come from the GitHub API and are cached under data/derived/repos. "
        "They are external_observation, not creator_claim and not agent inference. "
        "Rebuildable from the cache; verify before relying on them.",
        "",
        f"## Repositories ({len(rows)})",
        "",
    ]
    if rows:
        lines.extend(
            [
                "| Repository | URL | Description | Stars | Language | License "
                "| Archived | Last push | Videos | Sources |",
                "|---|---|---|---:|---|---|---|---|---:|---|",
            ]
        )
        lines.extend(_render_row(generated_prefix, row) for row in rows)
        lines.append("")
    else:
        lines.extend(["No GitHub repositories found.", ""])

    if failures:
        lines.extend([f"## Failed ({len(failures)})", ""])
        lines.extend(
            f"- {escape(ref.url, table=True)}: {escape(error, table=True)}"
            for ref, error in failures
        )
        lines.append("")

    if skipped:
        lines.extend([f"## Skipped non-GitHub ({len(skipped)})", ""])
        lines.extend(f"- {escape(ref.url, table=True)}" for ref in skipped)
        lines.append("")

    return GeneratedDocument(path=f"{slug}.md", content="\n".join(lines))


class RepoCatalogRunner:
    NAMESPACE = "repos"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        fetcher: RepoMetadataFetcher,
        cache: RepoCacheStore,
        generated_prefix: str = "_generated/evidence-vault",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packets = packets
        self._fetcher = fetcher
        self._cache = cache
        self._generated_prefix = generated_prefix.strip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, query: RepoCatalogQuery) -> RepoCatalogReport:
        selected = self._select(query)
        collected = _collect(selected)
        results: list[RepoCatalogItemResult] = []
        rows: list[_RepoRow] = []
        failures: list[tuple[RepoRef, str]] = []
        for key in sorted(collected.github):
            ref = collected.github[key]
            packets = collected.mentions[key]
            cache_key = _cache_key(ref)
            cached = None if query.refresh else self._load_cached(cache_key)
            if cached is not None:
                rows.append(_RepoRow(ref=ref, metadata=cached, packets=packets))
                results.append(self._result(ref, "skipped_cached"))
                continue
            if query.dry_run:
                results.append(self._result(ref, "fetched"))
                continue
            try:
                metadata = RepoMetadata.from_payload(self._fetcher.fetch(ref.owner, ref.repository))
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                failures.append((ref, message))
                results.append(self._result(ref, "failed", error=message))
                continue
            self._cache.save(cache_key, metadata.to_payload())
            rows.append(_RepoRow(ref=ref, metadata=metadata, packets=packets))
            results.append(self._result(ref, "fetched"))

        rows.sort(key=lambda row: (-len(row.packets), row.metadata.full_name.casefold()))
        note = None
        if not query.dry_run:
            note = _render_note(
                collection=query.collection,
                slug=slugify(query.collection),
                generated_at=self._clock(),
                generated_prefix=self._generated_prefix,
                video_count=len(selected),
                rows=rows,
                failures=failures,
                skipped=collected.other,
            )
        return RepoCatalogReport(
            collection=query.collection,
            repos_found=len(collected.github),
            fetched=sum(item.status == "fetched" for item in results),
            skipped_cached=sum(item.status == "skipped_cached" for item in results),
            failed=sum(item.status == "failed" for item in results),
            skipped_non_github=len(collected.other),
            dry_run=query.dry_run,
            note=note,
            results=tuple(results),
        )

    def _select(self, query: RepoCatalogQuery) -> list[SourcePacket]:
        collection = query.collection.casefold()
        latest = latest_capture_per_source(self._packets.iter_packets())
        selected = [
            packet
            for packet in latest.values()
            if collection in {item.name.casefold() for item in packet.collections}
        ]
        selected.sort(key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id))
        if query.limit is not None:
            selected = selected[: query.limit]
        return selected

    def _load_cached(self, key: str) -> RepoMetadata | None:
        try:
            payload = self._cache.load(key)
            if payload is None:
                return None
            return RepoMetadata.from_payload(payload)
        except Exception:
            return None

    @staticmethod
    def _result(
        ref: RepoRef,
        status: Literal["fetched", "skipped_cached", "failed"],
        *,
        error: str | None = None,
    ) -> RepoCatalogItemResult:
        return RepoCatalogItemResult(
            repository=ref.name,
            url=ref.url,
            status=status,
            error=error,
        )
