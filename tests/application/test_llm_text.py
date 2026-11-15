from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.llm_cache import FileSystemLlmCache
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.llm_text import build_source_prompt, extract_json_object
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def add_packet(root: Path, external_id: str, *, desc: str, author: str = "author") -> SourcePacket:
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
                captured_at=NOW,
                collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
                collections=(CollectionMembership(name="Нейронки"),),
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(b"caption"),
                    ),
                ),
                metadata={
                    "saved_index": {
                        "desc": desc,
                        "author": author,
                        "comments": [{"text": "first comment"}, {"text": "   "}],
                    }
                },
            )
        )
        .packet
    )


def test_llm_cache_load_returns_none_for_corrupt_entry(tmp_path: Path) -> None:
    cache = FileSystemLlmCache(tmp_path)
    path = tmp_path / "llm" / "k.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert cache.load("k") is None


def test_extract_json_object_handles_messy_text() -> None:
    messy = (
        "Sure! Here is the result:\n"
        "```json\n"
        '{"title": "a {brace} in a string", "summary": "ok", '
        '"category": "ai", "entities": [], "links": [], "tags": ["x"]}\n'
        "```\n"
        "Let me know if you need more."
    )

    parsed = extract_json_object(messy)

    assert parsed["title"] == "a {brace} in a string"
    assert parsed["tags"] == ["x"]


def test_extract_json_object_rejects_text_without_object() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json_object("no json here at all")


def test_build_source_prompt_lists_url_author_description_and_comments(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="trading bot idea")

    prompt = build_source_prompt(packet)

    assert "URL: https://www.tiktok.com/@x/video/one" in prompt
    assert "Author: author" in prompt
    assert "Description: trading bot idea" in prompt
    assert "Comments:" in prompt
    assert "- first comment" in prompt


def test_build_source_prompt_skips_blank_comments(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "two", desc="only description")

    prompt = build_source_prompt(packet)

    assert prompt.count("- ") == 1
