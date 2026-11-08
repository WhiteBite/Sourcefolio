from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemProjectFitIndex,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.llm_cache import FileSystemLlmCache
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.project_fit import (
    Project,
    ProjectFitQuery,
    ProjectFitResult,
    ProjectFitRunner,
    _build_user_prompt,
    _cache_key,
    _projects_key,
    parse_projects,
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

PROJECTS = (
    Project(id="midas", name="MidasAI", description="trading bot"),
    Project(id="mafiin", name="Mafiin", description="party game"),
)

MATCH_MIDAS = {
    "relevant": True,
    "summary": "useful",
    "matches": [
        {
            "project_id": "midas",
            "fit": "high",
            "feature": "backtest engine",
            "why": "matches trading",
        }
    ],
}

MATCH_MAFIIN = {
    "relevant": True,
    "summary": "party idea",
    "matches": [
        {
            "project_id": "mafiin",
            "fit": "high",
            "feature": "party mode",
            "why": "matches party game",
        }
    ],
}


class FakeChatClient:
    def __init__(
        self,
        responses: dict[str, dict[str, object]] | None = None,
        *,
        fail_on: str | None = None,
    ) -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self._responses = responses or {}
        self._fail_on = fail_on

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        self.prompts.append(user_prompt)
        if self._fail_on is not None and self._fail_on in user_prompt:
            raise RuntimeError("boom")
        for marker, payload in self._responses.items():
            if marker in user_prompt:
                return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        return json.dumps({"relevant": False, "summary": "", "matches": []})


class _FakeRecord:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeExtractions:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def iter_for_capture(self, capture_id: str) -> object:
        return iter(_FakeRecord(text) for text in self._texts)


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


def runner(
    root: Path,
    client: FakeChatClient,
    *,
    projects: tuple[Project, ...] = PROJECTS,
    extractions: FakeExtractions | None = None,
) -> ProjectFitRunner:
    return ProjectFitRunner(
        packets=FileSystemSourcePacketRepository(root),
        client=client,
        cache=FileSystemLlmCache(root / "derived", namespace=ProjectFitRunner.CACHE_NAMESPACE),
        index=FileSystemProjectFitIndex(root / "derived"),
        model="test-model",
        projects=projects,
        extractions=extractions,
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def test_project_fit_matches_one_project_and_marks_other_unmatched(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",)))

    assert report.items_scanned == 1
    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert report.unmatched == 0
    assert report.per_project == {"midas": 1, "mafiin": 0}
    notes = {note.path: note.content for note in report.notes}
    assert set(notes) == {"midasai.md", "mafiin.md", "index.md"}
    midas = notes["midasai.md"]
    assert "backtest engine" in midas
    assert "Fit: high" in midas
    assert "provenance: agent_inference" in midas
    assert "[[_generated/evidence-vault/source-cards/" in midas
    mafiin = notes["mafiin.md"]
    assert "## Mentioned but not for Mafiin (1)" in mafiin
    assert "[[_generated/evidence-vault/source-cards/" in mafiin


def test_project_fit_irrelevant_item_is_unmatched_and_absent(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="funny meme")
    client = FakeChatClient({"funny meme": {"relevant": False, "summary": "", "matches": []}})

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",)))

    assert report.unmatched == 1
    assert report.per_project == {"midas": 0, "mafiin": 0}
    notes = {note.path: note.content for note in report.notes}
    for path, content in notes.items():
        if path == "index.md":
            continue
        assert "[[_generated/evidence-vault/source-cards/" not in content
    assert "## Globally irrelevant (1)" in notes["index.md"]


def test_project_fit_ignores_unknown_project_ids(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    client = FakeChatClient(
        {
            "trading bot idea": {
                "relevant": True,
                "summary": "",
                "matches": [{"project_id": "ghost", "fit": "high", "feature": "x", "why": "y"}],
            }
        }
    )

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",)))

    assert report.unmatched == 1
    assert report.per_project == {"midas": 0, "mafiin": 0}


def test_project_fit_reprocesses_when_projects_change(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    runner(tmp_path, FakeChatClient({"trading bot idea": MATCH_MIDAS})).run(
        ProjectFitQuery(collections=("Нейронки",))
    )

    changed = (
        Project(id="midas", name="MidasAI", description="different description"),
        Project(id="mafiin", name="Mafiin", description="party game"),
    )
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})
    report = runner(tmp_path, client, projects=changed).run(
        ProjectFitQuery(collections=("Нейронки",))
    )

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1


def test_project_fit_skips_cached_on_rerun(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    runner(tmp_path, FakeChatClient({"trading bot idea": MATCH_MIDAS})).run(
        ProjectFitQuery(collections=("Нейронки",))
    )

    second = FakeChatClient({"trading bot idea": MATCH_MIDAS})
    report = runner(tmp_path, second).run(ProjectFitQuery(collections=("Нейронки",)))

    assert (report.processed, report.skipped_cached, report.failed) == (0, 1, 0)
    assert second.calls == 0
    assert report.per_project == {"midas": 1, "mafiin": 0}


def test_project_fit_refresh_requeries(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    runner(tmp_path, FakeChatClient({"trading bot idea": MATCH_MIDAS})).run(
        ProjectFitQuery(collections=("Нейронки",))
    )

    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})
    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",), refresh=True))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1


def test_project_fit_isolates_failures(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="good")
    add_packet(tmp_path, "two", desc="bad")
    client = FakeChatClient({"good": MATCH_MIDAS}, fail_on="bad")

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",)))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 1)
    assert report.per_project == {"midas": 1, "mafiin": 0}
    assert len(report.notes) == 3
    cache_files = [
        path
        for path in (tmp_path / "derived" / "project-fit").glob("*.json")
        if path.name != "index.json"
    ]
    assert len(cache_files) == 1


def test_project_fit_dry_run_writes_nothing(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",), dry_run=True))

    assert report.dry_run is True
    assert report.notes == ()
    assert client.calls == 0
    assert not (tmp_path / "derived" / "project-fit").exists()


def test_project_fit_treats_corrupt_cache_as_miss(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="trading bot idea")
    cache_root = tmp_path / "derived" / "project-fit"
    cache_root.mkdir(parents=True)
    prompt = _build_user_prompt(packet, PROJECTS, "")
    key = _cache_key(packet, prompt)
    (cache_root / f"{key}.json").write_text("{not json", encoding="utf-8")

    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})
    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",)))

    assert (report.processed, report.skipped_cached, report.failed) == (1, 0, 0)
    assert client.calls == 1
    payload = json.loads((cache_root / f"{key}.json").read_text("utf-8"))
    assert payload["relevant"] is True


def test_project_fit_uses_derived_ocr_text(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    client = FakeChatClient(
        {
            "slide text": {
                "relevant": True,
                "summary": "",
                "matches": [
                    {
                        "project_id": "midas",
                        "fit": "medium",
                        "feature": "ocr feature",
                        "why": "from slide",
                    }
                ],
            }
        }
    )

    report = runner(tmp_path, client, extractions=FakeExtractions(["slide text"])).run(
        ProjectFitQuery(collections=("Нейронки",))
    )

    assert report.per_project == {"midas": 1, "mafiin": 0}


def test_project_fit_dedupes_across_collections(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", collection="Нейронки", desc="trading bot idea")
    add_packet(tmp_path, "two", collection="Фильмы", desc="trading bot idea")
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки", "Фильмы")))

    assert report.items_scanned == 2
    assert report.per_project == {"midas": 2, "mafiin": 0}


def test_project_fit_keeps_latest_capture_per_source(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", collection="Нейронки", desc="trading bot idea")
    add_packet(
        tmp_path,
        "one",
        collection="Фильмы",
        desc="trading bot idea",
        captured_at=NOW.replace(hour=13),
    )
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки", "Фильмы")))

    assert report.items_scanned == 1
    assert report.per_project == {"midas": 1, "mafiin": 0}


def test_project_fit_result_parses_messy_text() -> None:
    messy = (
        "Sure! Here is the result:\n"
        "```json\n"
        '{"relevant": true, "summary": "ok", "matches": '
        '[{"project_id": "midas", "fit": "HIGH", "feature": "a {brace}", "why": "because"}]}\n'
        "```\n"
        "Let me know if you need more."
    )

    result = ProjectFitResult.from_text(messy)

    assert result.relevant is True
    assert result.matches[0].project_id == "midas"
    assert result.matches[0].fit == "high"
    assert result.matches[0].feature == "a {brace}"


def test_project_fit_result_drops_matches_when_irrelevant() -> None:
    result = ProjectFitResult.from_payload(
        {
            "relevant": False,
            "summary": "",
            "matches": [{"project_id": "midas", "fit": "high", "feature": "x", "why": "y"}],
        }
    )

    assert result.relevant is False
    assert result.matches == ()


def test_parse_projects_validates_shape() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        parse_projects({"projects": []})
    with pytest.raises(ValueError, match="duplicate"):
        parse_projects(
            {
                "projects": [
                    {"id": "a", "name": "A", "description": ""},
                    {"id": "a", "name": "B", "description": ""},
                ]
            }
        )
    with pytest.raises(ValueError, match="non-empty id and name"):
        parse_projects({"projects": [{"id": "", "name": "A", "description": ""}]})

    projects = parse_projects({"projects": [{"id": "a", "name": "A", "description": "d"}]})
    assert projects == (Project(id="a", name="A", description="d"),)


def test_parse_projects_reads_profile() -> None:
    projects = parse_projects(
        {"projects": [{"id": "a", "name": "A", "description": "d", "profile": "  human notes  "}]}
    )

    assert projects == (Project(id="a", name="A", description="d", profile="human notes"),)


def test_project_fit_prompt_includes_profile(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="trading bot idea")
    projects = (
        Project(
            id="midas",
            name="MidasAI",
            description="trading bot",
            profile="Focus on backtesting and risk.",
        ),
        Project(id="mafiin", name="Mafiin", description="party game"),
    )

    prompt = _build_user_prompt(packet, projects, "")

    assert "Profile: Focus on backtesting and risk." in prompt
    assert prompt.count("Profile:") == 1


def test_project_fit_prompt_unchanged_without_profile(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="trading bot idea")

    prompt = _build_user_prompt(packet, PROJECTS, "")

    assert "Profile:" not in prompt
    assert "- midas | MidasAI | trading bot" in prompt
    assert "- mafiin | Mafiin | party game" in prompt


def test_projects_key_changes_with_profile() -> None:
    base = (Project(id="midas", name="MidasAI", description="trading bot"),)
    with_profile = (
        Project(id="midas", name="MidasAI", description="trading bot", profile="extra"),
    )

    assert _projects_key(base) != _projects_key(with_profile)


def test_project_fit_accumulates_across_collections(tmp_path: Path) -> None:
    first = add_packet(tmp_path, "one", collection="A", desc="trading bot idea")
    second = add_packet(tmp_path, "two", collection="B", desc="party game idea")
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS, "party game idea": MATCH_MAFIIN})

    runner(tmp_path, client).run(ProjectFitQuery(collections=("A",)))
    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("B",)))

    assert report.per_project == {"midas": 1, "mafiin": 1}
    notes = {note.path: note.content for note in report.notes}
    assert "backtest engine" in notes["midasai.md"]
    assert "party mode" in notes["mafiin.md"]
    index = json.loads((tmp_path / "derived" / "project-fit" / "index.json").read_text("utf-8"))
    assert set(index["items"]) == {first.source_id, second.source_id}


def test_project_fit_rerun_keeps_index_unchanged(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    runner(tmp_path, FakeChatClient({"trading bot idea": MATCH_MIDAS})).run(
        ProjectFitQuery(collections=("Нейронки",))
    )
    index_path = tmp_path / "derived" / "project-fit" / "index.json"
    before = index_path.read_bytes()

    second = FakeChatClient({"trading bot idea": MATCH_MIDAS})
    report = runner(tmp_path, second).run(ProjectFitQuery(collections=("Нейронки",)))

    assert (report.processed, report.skipped_cached, report.failed) == (0, 1, 0)
    assert second.calls == 0
    assert index_path.read_bytes() == before


def test_project_fit_scans_all_collections_when_omitted(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", collection="A", desc="trading bot idea")
    add_packet(tmp_path, "two", collection="B", desc="trading bot idea")
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})

    report = runner(tmp_path, client).run(ProjectFitQuery())

    assert report.all_collections is True
    assert report.items_scanned == 2
    assert report.per_project == {"midas": 2, "mafiin": 0}


def test_project_fit_truncates_ocr_text(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    client = FakeChatClient({"trading bot idea": MATCH_MIDAS})

    runner(tmp_path, client, extractions=FakeExtractions(["A" * 5000 + "ZZZEND"])).run(
        ProjectFitQuery(collections=("Нейронки",), max_ocr_kb=1)
    )

    assert "ZZZEND" not in client.prompts[0]
    assert "A" * 100 in client.prompts[0]


def test_project_fit_refresh_overwrites_index_entry(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc="trading bot idea")
    runner(tmp_path, FakeChatClient({"trading bot idea": MATCH_MIDAS})).run(
        ProjectFitQuery(collections=("Нейронки",))
    )
    index_path = tmp_path / "derived" / "project-fit" / "index.json"
    first = json.loads(index_path.read_text("utf-8"))["items"][packet.source_id]
    assert first["matches"][0]["feature"] == "backtest engine"

    changed = {
        "relevant": True,
        "summary": "changed",
        "matches": [
            {"project_id": "midas", "fit": "low", "feature": "new feature", "why": "changed"}
        ],
    }
    runner(tmp_path, FakeChatClient({"trading bot idea": changed})).run(
        ProjectFitQuery(collections=("Нейронки",), refresh=True)
    )

    second = json.loads(index_path.read_text("utf-8"))["items"][packet.source_id]
    assert second["matches"][0]["feature"] == "new feature"
    assert second["summary"] == "changed"


def test_project_fit_note_heading_uses_summary(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc="trading bot idea")
    client = FakeChatClient(
        {
            "trading bot idea": {
                "relevant": True,
                "summary": "Great backtest idea\nmore detail",
                "matches": [
                    {
                        "project_id": "midas",
                        "fit": "high",
                        "feature": "backtest engine",
                        "why": "trading",
                    }
                ],
            }
        }
    )

    report = runner(tmp_path, client).run(ProjectFitQuery(collections=("Нейронки",)))

    notes = {note.path: note.content for note in report.notes}
    assert "### Great backtest idea" in notes["midasai.md"]
    assert "index.md" in notes
    assert "## Globally irrelevant (0)" in notes["index.md"]
