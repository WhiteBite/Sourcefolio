from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.llm_cache import FileSystemLlmCache
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.llm_catalog import (
    LlmCatalogEntry,
    LlmCatalogQuery,
    LlmCatalogRunner,
    _cache_key,
    normalize_title,
)
from evidence_vault.application.llm_enrich import _build_user_prompt
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

MATRIX = {
    "title": "The Matrix",
    "year": "1999",
    "kind": "film",
    "confidence": "high",
    "note": "",
}


class FakeCatalogClient:
    def __init__(
        self,
        responses: dict[str, dict[str, object]] | None = None,
        *,
        fail_on: str | None = None,
    ) -> None:
        self.calls = 0
        self._responses = responses or {}
        self._fail_on = fail_on

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self._fail_on is not None and self._fail_on in user_prompt:
            raise RuntimeError("boom")
        for marker, payload in self._responses.items():
            if marker in user_prompt:
                return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        return json.dumps(
            {"title": "", "year": None, "kind": "unknown", "confidence": "low", "note": ""}
        )


def add_packet(
    root: Path,
    external_id: str,
    *,
    collection: str = "Фильмы",
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


def runner(root: Path, client: FakeCatalogClient) -> LlmCatalogRunner:
    return LlmCatalogRunner(
        packets=FileSystemSourcePacketRepository(root),
        client=client,
        cache=FileSystemLlmCache(root / "derived", namespace="llm-catalog"),
        model="test-model",
        clock=lambda: NOW,
    )


def test_llm_catalog_dedupes_same_work(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="matrix")
    add_packet(tmp_path, "two", desc="matrix")
    client = FakeCatalogClient({"matrix": MATRIX})

    report = runner(tmp_path, client).run(LlmCatalogQuery(collection="Фильмы"))

    assert (report.processed, report.skipped_cached, report.failed) == (2, 0, 0)
    assert report.catalog_size == 1
    assert report.unresolved == 0
    assert report.note is not None
    assert report.note.path == "filmy.md"
    content = report.note.content
    assert content.count("### The Matrix (1999)") == 1
    assert content.count("[[_generated/evidence-vault/source-cards/") == 2
    assert "- Videos: 2" in content
    assert "provenance: agent_inference" in content


def test_llm_catalog_puts_empty_title_in_unresolved(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="not-a-work")
    client = FakeCatalogClient(
        {
            "not-a-work": {
                "title": "",
                "year": None,
                "kind": "unknown",
                "confidence": "low",
                "note": "not a film",
            }
        }
    )

    report = runner(tmp_path, client).run(LlmCatalogQuery(collection="Фильмы"))

    assert report.catalog_size == 0
    assert report.unresolved == 1
    assert report.note is not None
    content = report.note.content
    assert "## Works (0)" in content
    assert "## Unresolved / not a work (1)" in content
    assert "not a film" in content


def test_llm_catalog_skips_cached_on_rerun(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="matrix")
    runner(tmp_path, FakeCatalogClient({"matrix": MATRIX})).run(
        LlmCatalogQuery(collection="Фильмы")
    )

    second = FakeCatalogClient({"matrix": MATRIX})
    report = runner(tmp_path, second).run(LlmCatalogQuery(collection="Фильмы"))

    assert (report.processed, report.skipped_cached, report.failed) == (0, 1, 0)
    assert second.calls == 0
    assert report.catalog_size == 1
    assert report.note is not None
    assert "### The Matrix (1999)" in report.note.content


def test_llm_catalog_refresh_reprocesses(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="matrix")
    runner(tmp_path, FakeCatalogClient({"matrix": MATRIX})).run(
        LlmCatalogQuery(collection="Фильмы")
    )

    client = FakeCatalogClient({"matrix": MATRIX})
    report = runner(tmp_path, client).run(LlmCatalogQuery(collection="Фильмы", refresh=True))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1


def test_llm_catalog_isolates_failures(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="good")
    add_packet(tmp_path, "two", desc="bad")
    client = FakeCatalogClient({"good": MATRIX}, fail_on="bad")

    report = runner(tmp_path, client).run(LlmCatalogQuery(collection="Фильмы"))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 1)
    assert report.note is not None
    assert "## Failed" in report.note.content
    assert "boom" in report.note.content
    assert len(list((tmp_path / "derived" / "llm-catalog").glob("*.json"))) == 1


def test_llm_catalog_treats_corrupt_cache_as_miss(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="matrix")
    cache_root = tmp_path / "derived" / "llm-catalog"
    cache_root.mkdir(parents=True)
    key = _cache_key(packet, _build_user_prompt(packet))
    (cache_root / f"{key}.json").write_text("{not json", encoding="utf-8")

    client = FakeCatalogClient({"matrix": MATRIX})
    report = runner(tmp_path, client).run(LlmCatalogQuery(collection="Фильмы"))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1
    payload = json.loads((cache_root / f"{key}.json").read_text("utf-8"))
    assert payload["title"] == "The Matrix"


def test_llm_catalog_dry_run_writes_nothing(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="matrix")
    client = FakeCatalogClient({"matrix": MATRIX})

    report = runner(tmp_path, client).run(LlmCatalogQuery(collection="Фильмы", dry_run=True))

    assert report.dry_run is True
    assert report.note is None
    assert client.calls == 0
    assert not (tmp_path / "derived" / "llm-catalog").exists()


def test_llm_catalog_entry_parses_messy_text() -> None:
    messy = (
        "Sure! Here is the result:\n"
        "```json\n"
        '{"title": "A {brace} Film", "year": 1999, "kind": "Film", '
        '"confidence": "HIGH", "note": "ok"}\n'
        "```\n"
        "Let me know if you need more."
    )

    entry = LlmCatalogEntry.from_text(messy)

    assert entry.title == "A {brace} Film"
    assert entry.year == "1999"
    assert entry.kind == "film"
    assert entry.confidence == "high"


def test_llm_catalog_entry_normalizes_invalid_kind_and_confidence() -> None:
    entry = LlmCatalogEntry.from_payload(
        {"title": "X", "year": None, "kind": "cartoon", "confidence": "certain", "note": ""}
    )

    assert entry.kind == "unknown"
    assert entry.confidence == "low"
    assert entry.year is None


def test_normalize_title_strips_punctuation_and_case() -> None:
    assert normalize_title("  «The Matrix»  ") == "the matrix"
    assert normalize_title("Spider-Man: No Way Home.") == "spider-man: no way home"
    assert normalize_title("   ") == ""
