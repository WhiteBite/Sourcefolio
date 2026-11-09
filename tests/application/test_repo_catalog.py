from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import JsonValue

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.llm_cache import FileSystemLlmCache
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.repo_catalog import (
    RepoCatalogQuery,
    RepoCatalogRunner,
    RepoMetadata,
    parse_repo_ref,
)
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def payload(full_name: str, **extra: JsonValue) -> dict[str, JsonValue]:
    data: dict[str, JsonValue] = {
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": "",
        "stars": 0,
        "language": "",
        "license": "",
        "archived": False,
        "pushed_at": "",
        "topics": [],
    }
    data.update(extra)
    return data


class FakeFetcher:
    def __init__(
        self,
        payloads: Mapping[str, Mapping[str, JsonValue]] | None = None,
        *,
        fail_on: str | None = None,
        error: str = "repository not found (404): missing, renamed, or private",
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self._payloads = payloads or {}
        self._fail_on = fail_on
        self._error = error

    def fetch(self, owner: str, repository: str) -> Mapping[str, JsonValue]:
        self.calls.append((owner, repository))
        name = f"{owner}/{repository}"
        if self._fail_on is not None and self._fail_on.casefold() == name.casefold():
            raise RuntimeError(self._error)
        return dict(self._payloads.get(name.casefold(), payload(name)))


def add_packet(
    root: Path,
    external_id: str,
    *,
    collection: str = "Нейронки",
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
                collections=(CollectionMembership(name=collection),),
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


def runner(root: Path, fetcher: FakeFetcher) -> RepoCatalogRunner:
    return RepoCatalogRunner(
        packets=FileSystemSourcePacketRepository(root),
        fetcher=fetcher,
        cache=FileSystemLlmCache(root / "derived", namespace=RepoCatalogRunner.NAMESPACE),
        clock=lambda: NOW,
    )


def cache_path(root: Path, name: str) -> Path:
    digest = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:16]
    return root / "derived" / "repos" / f"{digest}.json"


def test_repo_catalog_dedupes_same_repo_across_videos(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="see https://github.com/owner/repo.git")
    add_packet(tmp_path, "two", desc="(https://github.com/OWNER/repo.)")
    fetcher = FakeFetcher({"owner/repo": payload("owner/repo", stars=42, language="Python")})

    report = runner(tmp_path, fetcher).run(RepoCatalogQuery(collection="Нейронки"))

    assert report.repos_found == 1
    assert (report.fetched, report.skipped_cached, report.failed) == (1, 0, 0)
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][0].casefold() == "owner"
    assert fetcher.calls[0][1] == "repo"
    assert report.note is not None
    assert report.note.path == "neyronki.md"
    content = report.note.content
    assert "## Repositories (1)" in content
    assert content.count("| owner/repo ") == 1
    assert content.count("[[_generated/sourcefolio/source-cards/") == 2
    assert "| 42 |" in content
    assert "provenance: external_observation" in content


def test_repo_catalog_records_failures_and_continues(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/good")
    add_packet(tmp_path, "two", desc="https://github.com/owner/bad")
    fetcher = FakeFetcher(fail_on="owner/bad")

    report = runner(tmp_path, fetcher).run(RepoCatalogQuery(collection="Нейронки"))

    assert (report.repos_found, report.fetched, report.failed) == (2, 1, 1)
    failed = [item for item in report.results if item.status == "failed"]
    assert len(failed) == 1
    assert failed[0].repository == "owner/bad"
    assert failed[0].error is not None and "404" in failed[0].error
    assert report.note is not None
    content = report.note.content
    assert "## Repositories (1)" in content
    assert "## Failed (1)" in content
    assert "https://github.com/owner/bad" in content
    assert cache_path(tmp_path, "owner/good").is_file()
    assert not cache_path(tmp_path, "owner/bad").exists()


def test_repo_catalog_skips_cached_on_rerun(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    runner(tmp_path, FakeFetcher()).run(RepoCatalogQuery(collection="Нейронки"))

    second = FakeFetcher()
    report = runner(tmp_path, second).run(RepoCatalogQuery(collection="Нейронки"))

    assert (report.fetched, report.skipped_cached, report.failed) == (0, 1, 0)
    assert second.calls == []
    assert report.note is not None
    assert "## Repositories (1)" in report.note.content


def test_repo_catalog_refresh_refetches(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    runner(tmp_path, FakeFetcher()).run(RepoCatalogQuery(collection="Нейронки"))

    second = FakeFetcher()
    report = runner(tmp_path, second).run(RepoCatalogQuery(collection="Нейронки", refresh=True))

    assert (report.fetched, report.skipped_cached) == (1, 0)
    assert len(second.calls) == 1


def test_repo_catalog_treats_corrupt_cache_as_miss(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    path = cache_path(tmp_path, "owner/repo")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    fetcher = FakeFetcher()
    report = runner(tmp_path, fetcher).run(RepoCatalogQuery(collection="Нейронки"))

    assert (report.fetched, report.skipped_cached, report.failed) == (1, 0, 0)
    assert len(fetcher.calls) == 1
    cached = json.loads(path.read_text(encoding="utf-8"))
    assert cached["full_name"] == "owner/repo"


def test_repo_catalog_dry_run_writes_nothing(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    fetcher = FakeFetcher()

    report = runner(tmp_path, fetcher).run(RepoCatalogQuery(collection="Нейронки", dry_run=True))

    assert report.dry_run is True
    assert report.note is None
    assert fetcher.calls == []
    assert report.results[0].status == "fetched"
    assert not (tmp_path / "derived" / "repos").exists()


def test_repo_catalog_skips_gitlab_and_counts(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="https://github.com/owner/repo")
    add_packet(tmp_path, "two", desc="https://gitlab.com/group/project")
    fetcher = FakeFetcher()

    report = runner(tmp_path, fetcher).run(RepoCatalogQuery(collection="Нейронки"))

    assert report.repos_found == 1
    assert report.skipped_non_github == 1
    assert fetcher.calls == [("owner", "repo")]
    assert report.note is not None
    content = report.note.content
    assert "## Skipped non-GitHub (1)" in content
    assert "https://gitlab.com/group/project" in content


def test_repo_catalog_counts_each_video_once_per_repo(tmp_path: Path) -> None:
    add_packet(
        tmp_path,
        "one",
        desc="https://github.com/owner/repo again https://github.com/owner/repo",
    )

    report = runner(tmp_path, FakeFetcher()).run(RepoCatalogQuery(collection="Нейронки"))

    assert report.repos_found == 1
    assert report.note is not None
    assert report.note.content.count("[[_generated/sourcefolio/source-cards/") == 1


def test_parse_repo_ref_tolerates_punctuation_and_git_suffix() -> None:
    assert parse_repo_ref("https://github.com/owner/repo.git") is not None
    assert parse_repo_ref("https://github.com/owner/repo.git").name == "owner/repo"
    assert parse_repo_ref("https://github.com/owner/repo.").name == "owner/repo"
    assert parse_repo_ref("github.com/Owner/Repo").key == "github.com/owner/repo"
    assert parse_repo_ref("https://www.github.com/owner/repo").host == "github.com"
    assert parse_repo_ref("https://gitlab.com/group/project").host == "gitlab.com"
    assert parse_repo_ref("not a url") is None
    assert parse_repo_ref("https://github.com/only-owner") is None


def test_repo_metadata_from_payload_requires_full_name() -> None:
    with pytest.raises(ValueError, match="full_name"):
        RepoMetadata.from_payload({"stars": 1})

    metadata = RepoMetadata.from_payload(
        payload("owner/repo", stars=7, license="MIT", topics=["ai", ""], archived=True)
    )
    assert metadata.stars == 7
    assert metadata.license == "MIT"
    assert metadata.topics == ("ai",)
    assert metadata.archived is True
    assert RepoMetadata.from_payload(metadata.to_payload()) == metadata
