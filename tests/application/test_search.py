from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.search import SearchIndexBuilder
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CaptureStatus,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def add_packet(
    root: Path,
    external_id: str,
    title: str,
    collection: str,
    caption: str,
    *,
    status: CaptureStatus = CaptureStatus.COMPLETE,
) -> None:
    use_case = ArchiveCapture(
        artifacts=FileSystemArtifactStore(root),
        packets=FileSystemSourcePacketRepository(root),
    )
    use_case.handle(
        ArchiveCaptureCommand(
            source=SourceIdentity(
                platform="tiktok",
                kind=SourceKind.VIDEO,
                external_id=external_id,
                canonical_url=f"https://www.tiktok.com/@x/video/{external_id}",
            ),
            captured_at=NOW + timedelta(minutes=int(external_id)),
            collector=Provenance(
                actor_kind=ActorKind.TOOL,
                actor_id="test",
                method="test",
            ),
            title=title,
            collections=(CollectionMembership(name=collection),),
            status=status,
            limitations=("missing media",) if status == CaptureStatus.PARTIAL else (),
            artifacts=(
                ArtifactPayload(
                    role=ArtifactRole.CAPTION_TEXT,
                    media_type="text/plain; charset=utf-8",
                    stream=BytesIO(caption.encode("utf-8")),
                ),
            ),
        )
    )


def build_index(root: Path, **kwargs):
    return SearchIndexBuilder(
        packets=FileSystemSourcePacketRepository(root),
        artifacts=FileSystemArtifactStore(root),
        **kwargs,
    ).build()


def test_lexical_search_supports_russian_and_title_collection_ranking(
    tmp_path: Path,
) -> None:
    add_packet(tmp_path, "1", "Нейронный старт", "Нейронки", "агенты и репозитории")
    add_packet(tmp_path, "2", "Фильм", "Фильмы", "нейронки в кино")
    add_packet(tmp_path, "3", "Нейронки", "Нейронки", "agent")
    index = build_index(tmp_path)

    results = index.search("НЕЙРОНКИ")

    assert [result.title for result in results] == [
        "Нейронки",
        "Нейронный старт",
        "Фильм",
    ]
    assert results[0].score > results[-1].score


def test_search_requires_all_terms_and_supports_filters(tmp_path: Path) -> None:
    add_packet(tmp_path, "1", "Нейронный старт", "Нейронки", "агенты и репозитории")
    add_packet(tmp_path, "2", "Фильм", "Фильмы", "нейронки в кино")
    index = build_index(tmp_path)

    assert [result.title for result in index.search("нейронки репозитории")] == ["Нейронный старт"]
    assert [result.title for result in index.search("нейронки", collection="фильмы")] == ["Фильм"]
    assert index.search("отсутствует") == ()


def test_search_respects_status_and_bounded_artifact_reads(tmp_path: Path) -> None:
    add_packet(
        tmp_path,
        "1",
        "Partial item",
        "Идеи",
        "needle text that is too large",
        status=CaptureStatus.PARTIAL,
    )
    index = build_index(tmp_path, max_text_artifact_bytes=8)

    assert index.search("needle") == ()
    assert len(index.search("missing", status=CaptureStatus.PARTIAL)) == 1
    with pytest.raises(ValueError):
        index.search(" ")
    with pytest.raises(ValueError):
        index.search("needle", limit=0)
