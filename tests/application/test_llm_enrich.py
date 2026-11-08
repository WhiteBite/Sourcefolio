from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.llm_cache import FileSystemLlmCache
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.llm_enrich import (
    LlmEnrichQuery,
    LlmEnrichRunner,
    _build_user_prompt,
    _cache_key,
    extract_json_object,
)
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeChatClient:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls = 0
        self._fail_on = fail_on

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self._fail_on is not None and self._fail_on in user_prompt:
            raise RuntimeError("boom")
        url = ""
        for line in user_prompt.splitlines():
            if line.startswith("URL: "):
                url = line[len("URL: ") :]
        payload = {
            "title": f"Title {self.calls}",
            "summary": f"Summary {self.calls}",
            "category": "ai",
            "entities": [{"type": "tool", "value": "PyTorch"}],
            "links": [url] if url else [],
            "tags": ["нейронки"],
        }
        return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


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
                metadata={
                    "saved_index": {
                        "author": "author",
                        "desc": desc,
                        "comments": [{"text": "класс", "author": "bob", "likes": 2}],
                    }
                },
            )
        )
        .packet
    )


def runner(root: Path, client: FakeChatClient) -> LlmEnrichRunner:
    return LlmEnrichRunner(
        packets=FileSystemSourcePacketRepository(root),
        client=client,
        cache=FileSystemLlmCache(root / "derived"),
        model="test-model",
        clock=lambda: NOW,
    )


def test_llm_enrich_writes_cache_and_deduplicated_note(tmp_path: Path) -> None:
    first = add_packet(tmp_path, "one")
    second = add_packet(tmp_path, "two")
    client = FakeChatClient()

    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки"))

    assert (report.processed, report.skipped_cached, report.failed) == (2, 0, 0)
    assert client.calls == 2
    assert report.note is not None
    assert report.note.path == "neyronki.md"

    cache_root = tmp_path / "derived" / "llm"
    for packet in (first, second):
        key = _cache_key(packet, _build_user_prompt(packet))
        payload = json.loads((cache_root / f"{key}.json").read_text("utf-8"))
        assert set(payload) == {"title", "summary", "category", "entities", "links", "tags"}
        assert payload["entities"] == [{"type": "tool", "value": "PyTorch"}]

    content = report.note.content
    assert "Machine inference" in content
    assert "not creator_claim" in content
    assert "## Entities" in content
    assert "## Links" in content
    assert "## Categories" in content
    assert content.count("| tool | PyTorch |") == 1
    assert first.source.canonical_url in content
    assert second.source.canonical_url in content
    assert "[[_generated/evidence-vault/source-cards/" in content


def test_llm_enrich_skips_cached_items_on_rerun(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    add_packet(tmp_path, "two")
    runner(tmp_path, FakeChatClient()).run(LlmEnrichQuery(collection="Нейронки"))

    second_client = FakeChatClient()
    report = runner(tmp_path, second_client).run(LlmEnrichQuery(collection="Нейронки"))

    assert (report.processed, report.skipped_cached, report.failed) == (0, 2, 0)
    assert second_client.calls == 0
    assert report.note is not None
    assert "Title 1" in report.note.content


def test_llm_enrich_skips_recaptured_source_by_content(tmp_path: Path) -> None:
    first = add_packet(tmp_path, "one")
    client = FakeChatClient()
    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки"))
    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1

    recapture = add_packet(tmp_path, "one", captured_at=NOW.replace(hour=13))
    assert recapture.source_id == first.source_id
    assert recapture.capture_id != first.capture_id

    second_client = FakeChatClient()
    report = runner(tmp_path, second_client).run(LlmEnrichQuery(collection="Нейронки"))

    assert (report.processed, report.skipped_cached, report.failed) == (0, 1, 0)
    assert second_client.calls == 0


def test_llm_enrich_reprocesses_when_content_changes(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="первый")
    runner(tmp_path, FakeChatClient()).run(LlmEnrichQuery(collection="Нейронки"))

    add_packet(tmp_path, "one", desc="второй", captured_at=NOW.replace(hour=13))
    client = FakeChatClient()
    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки"))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1


def test_llm_enrich_processes_new_source(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    runner(tmp_path, FakeChatClient()).run(LlmEnrichQuery(collection="Нейронки"))

    add_packet(tmp_path, "two")
    client = FakeChatClient()
    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки"))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 1, 0)
    assert client.calls == 1


def test_llm_enrich_refresh_requeries_and_overwrites_cache(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one")
    runner(tmp_path, FakeChatClient()).run(LlmEnrichQuery(collection="Нейронки"))

    client = FakeChatClient()
    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки", refresh=True))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1
    cache_root = tmp_path / "derived" / "llm"
    assert len(list(cache_root.glob("*.json"))) == 1
    key = _cache_key(packet, _build_user_prompt(packet))
    payload = json.loads((cache_root / f"{key}.json").read_text("utf-8"))
    assert payload["title"] == "Title 1"


def test_llm_enrich_dry_run_writes_nothing(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    client = FakeChatClient()

    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки", dry_run=True))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert report.dry_run is True
    assert report.note is None
    assert client.calls == 0
    assert not (tmp_path / "derived" / "llm").exists()


def test_llm_enrich_isolates_failures(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="good")
    add_packet(tmp_path, "two", desc="bad")
    client = FakeChatClient(fail_on="bad")

    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки"))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 1)
    assert report.note is not None
    assert "## Failed" in report.note.content
    assert "boom" in report.note.content
    assert len(list((tmp_path / "derived" / "llm").glob("*.json"))) == 1


def test_llm_enrich_limit_selects_latest_captures(tmp_path: Path) -> None:
    add_packet(tmp_path, "one")
    add_packet(tmp_path, "two")
    add_packet(tmp_path, "three")

    report = runner(tmp_path, FakeChatClient()).run(LlmEnrichQuery(collection="Нейронки", limit=2))

    assert report.processed == 2


def test_llm_enrich_treats_corrupt_cache_as_miss(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one")
    cache_root = tmp_path / "derived" / "llm"
    cache_root.mkdir(parents=True)
    key = _cache_key(packet, _build_user_prompt(packet))
    (cache_root / f"{key}.json").write_text("{not json", encoding="utf-8")

    client = FakeChatClient()
    report = runner(tmp_path, client).run(LlmEnrichQuery(collection="Нейронки"))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1
    payload = json.loads((cache_root / f"{key}.json").read_text("utf-8"))
    assert payload["title"] == "Title 1"


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
