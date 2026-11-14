from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sourcefolio.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemRegistryRouteStore,
    FileSystemSourcePacketRepository,
)
from sourcefolio.adapters.filesystem.llm_cache import FileSystemLlmCache
from sourcefolio.application.archive_capture import ArchiveCapture
from sourcefolio.application.commands import ArchiveCaptureCommand, ArtifactPayload
from sourcefolio.application.project_fit import Project
from sourcefolio.application.registry_extract import (
    RegistryExtractor,
    RegistryExtractQuery,
)
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.registry import (
    AnchorField,
    CandidateKind,
    FitLevel,
    RegistryRoute,
    RouteVerdict,
)
from sourcefolio.domain.source import (
    ArtifactRole,
    CollectionMembership,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

PROJECTS = (
    Project(id="sourcefolio", name="Sourcefolio", description="evidence archive"),
    Project(id="mafiin", name="Mafiin", description="party game"),
)

SINGLE = (PROJECTS[0],)

DESCRIPTION = "Alpha Tool is a useful library for working with text"


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
                return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"verdict": "insufficient_signal", "reason": "none", "mentions": []})


class FakeRecord:
    def __init__(self, extraction_id: str, text: str) -> None:
        self.extraction_id = extraction_id
        self.text = text


class FakeExtractions:
    def __init__(self, texts_by_capture: dict[str, list[tuple[str, str]]]) -> None:
        self._texts_by_capture = texts_by_capture

    def iter_for_capture(self, capture_id: str) -> object:
        return iter(
            FakeRecord(extraction_id, text)
            for extraction_id, text in self._texts_by_capture.get(capture_id, [])
        )


def add_packet(
    root: Path,
    external_id: str,
    *,
    collection: str = "Нейронки",
    desc: str = "текст",
    caption: bytes = b"caption",
    comments: list[dict[str, object]] | None = None,
    title: str | None = None,
    captured_at: datetime = NOW,
) -> SourcePacket:
    saved: dict[str, object] = {
        "author": "author",
        "desc": desc,
        "comments": comments
        if comments is not None
        else [{"text": "класс", "author": "bob", "likes": 2}],
    }
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
                title=title,
                artifacts=(
                    ArtifactPayload(
                        role=ArtifactRole.CAPTION_TEXT,
                        media_type="text/plain",
                        stream=BytesIO(caption),
                    ),
                ),
                metadata={"saved_index": saved},
            )
        )
        .packet
    )


def extractor(
    root: Path,
    client: FakeChatClient,
    *,
    projects: tuple[Project, ...] = PROJECTS,
    extractions: FakeExtractions | None = None,
) -> RegistryExtractor:
    return RegistryExtractor(
        packets=FileSystemSourcePacketRepository(root),
        artifacts=FileSystemArtifactStore(root),
        extractions=extractions or FakeExtractions({}),
        client=client,
        cache=FileSystemLlmCache(root / "derived", namespace="registry-extract"),
        routes=FileSystemRegistryRouteStore(root / "derived"),
        projects=projects,
        model="test-model",
        clock=lambda: NOW,
        retry_backoff_seconds=0.0,
    )


def stored_routes(root: Path) -> list[RegistryRoute]:
    return list(FileSystemRegistryRouteStore(root / "derived").iter_all())


def candidate(mentions: list[dict[str, object]]) -> dict[str, object]:
    return {"verdict": "candidate", "reason": "ok", "mentions": mentions}


def test_valid_quote_produces_mention_with_exact_offsets(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    client = FakeChatClient(
        {
            "Alpha Tool": candidate(
                [
                    {
                        "name": "Alpha Tool",
                        "kind": "tool",
                        "fit": "high",
                        "quote": "Alpha Tool",
                        "field": "description",
                        "comment_index": None,
                    }
                ]
            )
        }
    )

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.routes_processed == 1
    routes = stored_routes(tmp_path)
    assert len(routes) == 1
    route = routes[0]
    assert route.verdict == RouteVerdict.CANDIDATE
    assert len(route.mentions) == 1
    mention = route.mentions[0]
    assert mention.kind == CandidateKind.TOOL
    assert mention.fit == FitLevel.HIGH
    assert mention.anchor.quote == "Alpha Tool"
    start = DESCRIPTION.find("Alpha Tool")
    assert mention.anchor.start_char == start
    assert mention.anchor.end_char == start + len("Alpha Tool")


def test_paraphrased_quote_is_dropped_and_downgrades_to_insufficient(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    client = FakeChatClient(
        {
            "Alpha Tool": candidate(
                [
                    {
                        "name": "Beta Tool",
                        "kind": "tool",
                        "fit": "high",
                        "quote": "Beta Tool",
                        "field": "description",
                        "comment_index": None,
                    }
                ]
            )
        }
    )

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.dropped_anchors == 1
    assert report.verdicts["insufficient_signal"] == 1
    route = stored_routes(tmp_path)[0]
    assert route.verdict == RouteVerdict.INSUFFICIENT_SIGNAL
    assert route.reason == "no verbatim anchor"
    assert route.mentions == ()


def test_packet_without_text_gets_deterministic_route_and_no_client_call(tmp_path: Path) -> None:
    add_packet(
        tmp_path,
        "one",
        desc="",
        comments=[],
        caption=b"WEBVTT\n\n00:00 --> 00:01\n\n1\n",
    )
    client = FakeChatClient()

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert client.calls == 0
    assert report.routes_processed == 1
    route = stored_routes(tmp_path)[0]
    assert route.verdict == RouteVerdict.INSUFFICIENT_SIGNAL
    assert route.reason == "not enough text"
    assert route.provenance.actor_kind == ActorKind.TOOL
    assert route.provenance.actor_id == "registry-extract"
    assert route.provenance.method == "metadata"
    assert not (tmp_path / "derived" / "registry-extract").exists()


def test_invalid_verdict_is_coerced_to_insufficient_signal(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    client = FakeChatClient(
        {"Alpha Tool": {"verdict": "maybe", "reason": "unclear", "mentions": []}}
    )

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.verdicts["insufficient_signal"] == 1
    route = stored_routes(tmp_path)[0]
    assert route.verdict == RouteVerdict.INSUFFICIENT_SIGNAL
    assert route.reason == "invalid model response"


def test_cache_hit_skips_client(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    response = candidate(
        [
            {
                "name": "Alpha Tool",
                "kind": "tool",
                "fit": "high",
                "quote": "Alpha Tool",
                "field": "description",
                "comment_index": None,
            }
        ]
    )
    extractor(tmp_path, FakeChatClient({"Alpha Tool": response})).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )
    for path in (tmp_path / "derived" / "registry" / "routes").glob("*.json"):
        path.unlink()

    client = FakeChatClient({"Alpha Tool": response})
    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert client.calls == 0
    assert report.skipped_cached == 1
    assert report.routes_processed == 0
    assert len(stored_routes(tmp_path)) == 1


def test_refresh_calls_client_again(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    extractor(tmp_path, FakeChatClient({"Alpha Tool": candidate([])})).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    client = FakeChatClient({"Alpha Tool": candidate([])})
    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",), refresh=True)
    )

    assert client.calls == 1
    assert report.routes_processed == 1


def test_new_only_skips_existing_route_without_client_call(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    extractor(tmp_path, FakeChatClient({"Alpha Tool": candidate([])})).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    client = FakeChatClient({"Alpha Tool": candidate([])})
    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",), new_only=True)
    )

    assert client.calls == 0
    assert report.routes_processed == 0
    assert report.skipped_cached == 1
    assert report.results[0].status == "skipped"
    assert report.results[0].verdict is None
    assert report.results[0].error is None


def test_new_only_limit_frees_slot_for_new_packet(tmp_path: Path) -> None:
    older = add_packet(
        tmp_path,
        "older",
        desc="Repository https://github.com/Acme/Tool is useful for text and data work",
    )
    newer = add_packet(
        tmp_path,
        "newer",
        desc="Beta Tool is a useful library for working with text and data",
        captured_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
    )
    first = extractor(tmp_path, FakeChatClient()).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",), limit=1)
    )
    assert first.results[0].capture_id == older.capture_id

    client = FakeChatClient()
    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(
            projects=SINGLE,
            collections=("Нейронки",),
            limit=1,
            new_only=True,
        )
    )

    assert client.calls == 1
    assert report.routes_processed == 1
    assert report.skipped_cached == 1
    processed = [item for item in report.results if item.status == "processed"]
    skipped = [item for item in report.results if item.status == "skipped"]
    assert [item.capture_id for item in processed] == [newer.capture_id]
    assert [item.capture_id for item in skipped] == [older.capture_id]


def test_new_only_skips_routed_project_and_processes_unrouted_project(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    extractor(tmp_path, FakeChatClient()).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    client = FakeChatClient()
    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=PROJECTS, collections=("Нейронки",), new_only=True)
    )

    assert client.calls == 1
    assert report.routes_processed == 1
    assert report.skipped_cached == 1
    by_project = {item.project_id: item for item in report.results}
    assert by_project["sourcefolio"].status == "skipped"
    assert by_project["sourcefolio"].verdict is None
    assert by_project["mafiin"].status == "processed"


def test_new_only_false_keeps_existing_route_skip_behavior(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    extractor(tmp_path, FakeChatClient()).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    client = FakeChatClient()
    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",), new_only=False)
    )

    assert client.calls == 0
    assert report.routes_processed == 0
    assert report.skipped_cached == 1
    assert report.results[0].status == "skipped"
    assert report.results[0].verdict == RouteVerdict.INSUFFICIENT_SIGNAL.value


def test_new_only_with_refresh_still_skips_routed_pair(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    extractor(tmp_path, FakeChatClient()).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    client = FakeChatClient()
    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(
            projects=SINGLE,
            collections=("Нейронки",),
            new_only=True,
            refresh=True,
        )
    )

    assert client.calls == 0
    assert report.routes_processed == 0
    assert report.skipped_cached == 1


def test_repository_url_in_description_becomes_repo_mention(tmp_path: Path) -> None:
    desc = "Repository https://github.com/Acme/Tool is useful for text and data work"
    add_packet(tmp_path, "one", desc=desc)
    client = FakeChatClient({"https://github.com/Acme/Tool": candidate([])})

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.mentions == 1
    route = stored_routes(tmp_path)[0]
    assert route.verdict == RouteVerdict.CANDIDATE
    mention = route.mentions[0]
    assert mention.kind == CandidateKind.REPOSITORY
    assert mention.fit == FitLevel.LOW
    assert mention.candidate_key == "repo:github.com/acme/tool"
    assert mention.anchor.field == AnchorField.DESCRIPTION
    assert mention.anchor.quote == "https://github.com/Acme/Tool"
    start = desc.find("https://github.com/Acme/Tool")
    assert mention.anchor.start_char == start
    assert mention.anchor.end_char == start + len("https://github.com/Acme/Tool")


def test_same_packet_for_two_projects_produces_two_routes(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    response = candidate(
        [
            {
                "name": "Alpha Tool",
                "kind": "tool",
                "fit": "high",
                "quote": "Alpha Tool",
                "field": "description",
                "comment_index": None,
            }
        ]
    )
    client = FakeChatClient({"Alpha Tool": response})

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=PROJECTS, collections=("Нейронки",))
    )

    assert report.items_scanned == 1
    assert report.routes_processed == 2
    assert client.calls == 2
    assert {item.project_id for item in report.results} == {"sourcefolio", "mafiin"}
    assert len(stored_routes(tmp_path)) == 2


def test_dry_run_writes_nothing_and_returns_two_previews(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION)
    add_packet(tmp_path, "two", desc="Beta Tool это другая полезная библиотека для данных")
    client = FakeChatClient()

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",), dry_run=True)
    )

    assert report.dry_run is True
    assert client.calls == 0
    assert len(report.previews) == 2
    assert report.routes_processed == 2
    assert list((tmp_path / "derived").rglob("*.json")) == []


def test_ocr_and_caption_mentions_use_typed_locators(tmp_path: Path) -> None:
    packet = add_packet(
        tmp_path,
        "one",
        desc=DESCRIPTION,
        caption=b"WEBVTT\n\n00:00.000 --> 00:01.000\nCaption alpha text\n",
    )
    extraction_id = "ext_" + "a" * 64
    extractions = FakeExtractions({packet.capture_id: [(extraction_id, "Slide alpha tool text")]})
    response = candidate(
        [
            {
                "name": "Alpha Tool",
                "kind": "tool",
                "fit": "high",
                "quote": "Alpha Tool",
                "field": "description",
                "comment_index": None,
            },
            {
                "name": "Slide alpha",
                "kind": "technique",
                "fit": "medium",
                "quote": "Slide alpha tool text",
                "field": "ocr",
                "comment_index": None,
            },
            {
                "name": "Caption alpha",
                "kind": "pattern",
                "fit": "low",
                "quote": "Caption alpha text",
                "field": "caption",
                "comment_index": None,
            },
        ]
    )
    client = FakeChatClient({"Alpha Tool": response})

    report = extractor(tmp_path, client, extractions=extractions).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.dropped_anchors == 0
    assert report.mentions == 3
    route = stored_routes(tmp_path)[0]
    assert route.verdict == RouteVerdict.CANDIDATE
    anchors = {mention.anchor.field: mention.anchor for mention in route.mentions}
    assert anchors[AnchorField.DESCRIPTION].extraction_id is None
    assert anchors[AnchorField.DESCRIPTION].artifact_id is None
    ocr_anchor = anchors[AnchorField.OCR]
    assert ocr_anchor.extraction_id == extraction_id
    assert ocr_anchor.artifact_id is None
    assert ocr_anchor.start_char == 0
    assert ocr_anchor.end_char == len("Slide alpha tool text")
    caption_anchor = anchors[AnchorField.CAPTION]
    assert caption_anchor.artifact_id is not None
    assert caption_anchor.extraction_id is None
    assert caption_anchor.quote == "Caption alpha text"
    assert caption_anchor.start_char == 0
    assert caption_anchor.end_char == len("Caption alpha text")


def test_invalid_ocr_quote_is_dropped_and_counted(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc=DESCRIPTION)
    extraction_id = "ext_" + "b" * 64
    extractions = FakeExtractions({packet.capture_id: [(extraction_id, "Slide alpha tool text")]})
    response = candidate(
        [
            {
                "name": "Slide alpha",
                "kind": "technique",
                "fit": "medium",
                "quote": "Slide beta tool text",
                "field": "ocr",
                "comment_index": None,
            }
        ]
    )
    client = FakeChatClient({"Slide alpha": response})

    report = extractor(tmp_path, client, extractions=extractions).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.dropped_anchors == 1
    assert report.mentions == 0
    route = stored_routes(tmp_path)[0]
    assert route.verdict == RouteVerdict.INSUFFICIENT_SIGNAL
    assert route.reason == "no verbatim anchor"


def test_repository_url_in_ocr_and_caption_becomes_typed_mention(tmp_path: Path) -> None:
    packet = add_packet(
        tmp_path,
        "one",
        desc=DESCRIPTION,
        caption=b"WEBVTT\n\n00:00.000 --> 00:01.000\nSee https://github.com/Acme/Caption\n",
    )
    extraction_id = "ext_" + "c" * 64
    extractions = FakeExtractions(
        {packet.capture_id: [(extraction_id, "Repo https://github.com/Acme/Ocr is useful")]}
    )
    client = FakeChatClient({"Alpha Tool": candidate([])})

    report = extractor(tmp_path, client, extractions=extractions).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.mentions == 2
    route = stored_routes(tmp_path)[0]
    anchors = {mention.candidate_key: mention.anchor for mention in route.mentions}
    ocr_anchor = anchors["repo:github.com/acme/ocr"]
    assert ocr_anchor.field == AnchorField.OCR
    assert ocr_anchor.extraction_id == extraction_id
    assert ocr_anchor.artifact_id is None
    caption_anchor = anchors["repo:github.com/acme/caption"]
    assert caption_anchor.field == AnchorField.CAPTION
    assert caption_anchor.artifact_id is not None
    assert caption_anchor.extraction_id is None


def test_title_mention_uses_title_anchor(tmp_path: Path) -> None:
    add_packet(tmp_path, "one", desc=DESCRIPTION, title="Alpha Tool overview")
    client = FakeChatClient(
        {
            "Alpha Tool": candidate(
                [
                    {
                        "name": "Alpha Tool",
                        "kind": "tool",
                        "fit": "high",
                        "quote": "Alpha Tool",
                        "field": "title",
                        "comment_index": None,
                    }
                ]
            )
        }
    )

    report = extractor(tmp_path, client).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",))
    )

    assert report.dropped_anchors == 0
    assert report.mentions == 1
    route = stored_routes(tmp_path)[0]
    anchor = route.mentions[0].anchor
    assert anchor.field == AnchorField.TITLE
    assert anchor.start_char == 0
    assert anchor.end_char == len("Alpha Tool")
    assert anchor.extraction_id is None
    assert anchor.artifact_id is None
    assert anchor.comment_index is None


def test_ocr_budget_excludes_segment_that_alone_exceeds_budget(tmp_path: Path) -> None:
    packet = add_packet(tmp_path, "one", desc=DESCRIPTION)
    first = "ext_" + "d" * 64
    second = "ext_" + "e" * 64
    extractions = FakeExtractions(
        {
            packet.capture_id: [
                (first, "A" * 1100),
                (second, "Useful budget tool"),
            ]
        }
    )
    client = FakeChatClient(
        {
            "Alpha Tool": candidate(
                [
                    {
                        "name": "Useful budget tool",
                        "kind": "tool",
                        "fit": "high",
                        "quote": "Useful budget tool",
                        "field": "ocr",
                        "comment_index": None,
                    }
                ]
            )
        }
    )

    report = extractor(tmp_path, client, extractions=extractions).run(
        RegistryExtractQuery(projects=SINGLE, collections=("Нейронки",), max_ocr_kb=1)
    )

    assert report.dropped_anchors == 1
    assert report.mentions == 0
