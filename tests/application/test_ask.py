from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from evidence_vault.adapters.filesystem import (
    FileSystemArtifactStore,
    FileSystemSourcePacketRepository,
)
from evidence_vault.adapters.filesystem.llm_cache import FileSystemLlmCache
from evidence_vault.application.archive_capture import ArchiveCapture
from evidence_vault.application.ask import (
    AskQuery,
    AskRunner,
    ask_to_dict,
    render_ask_note,
)
from evidence_vault.application.commands import ArchiveCaptureCommand, ArtifactPayload
from evidence_vault.application.evidence_pack import EvidencePackBuilder
from evidence_vault.application.llm_ports import ChatClient
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
    def __init__(self, answer: str = "grounded answer", *, fail: bool = False) -> None:
        self.calls = 0
        self._answer = answer
        self._fail = fail

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self._fail:
            raise RuntimeError("boom")
        return self._answer


def add_packet(root: Path, external_id: str, *, desc: str = "trading bot idea") -> SourcePacket:
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
                metadata={"saved_index": {"desc": desc, "author": "author"}},
            )
        )
        .packet
    )


def runner(root: Path, client: ChatClient | None) -> AskRunner:
    archive = root / "archive"
    artifacts = FileSystemArtifactStore(archive)
    return AskRunner(
        pack_builder=EvidencePackBuilder(
            packets=FileSystemSourcePacketRepository(archive),
            artifacts=artifacts,
            artifact_locator=artifacts,
            clock=lambda: NOW,
        ),
        client=client,
        cache=FileSystemLlmCache(root / "derived", namespace=AskRunner.CACHE_NAMESPACE),
        model="test-model",
        clock=lambda: NOW,
        sleep=lambda _: None,
    )


def test_ask_retrieval_only_without_client(tmp_path: Path) -> None:
    add_packet(tmp_path / "archive", "one")

    report = runner(tmp_path, None).run(AskQuery(query="trading bot"))

    assert report.status == "retrieval_only"
    assert report.grounded is False
    assert report.answer == ""
    assert report.citations
    assert any("Retrieval-only" in item for item in report.limitations)


def test_ask_grounded_with_client(tmp_path: Path) -> None:
    add_packet(tmp_path / "archive", "one")
    client = FakeChatClient("the answer")

    report = runner(tmp_path, client).run(AskQuery(query="trading bot"))

    assert report.status == "ok"
    assert report.grounded is True
    assert report.answer == "the answer"
    assert client.calls == 1


def test_ask_cache_hit_skips_client(tmp_path: Path) -> None:
    add_packet(tmp_path / "archive", "one")
    runner(tmp_path, FakeChatClient("cached answer")).run(AskQuery(query="trading bot"))

    second = FakeChatClient("other answer")
    report = runner(tmp_path, second).run(AskQuery(query="trading bot"))

    assert second.calls == 0
    assert report.status == "ok"
    assert report.answer == "cached answer"


def test_ask_degraded_on_client_failure(tmp_path: Path) -> None:
    add_packet(tmp_path / "archive", "one")
    client = FakeChatClient(fail=True)

    report = runner(tmp_path, client).run(AskQuery(query="trading bot"))

    assert report.status == "degraded"
    assert report.grounded is False
    assert report.answer == ""
    assert any("LLM synthesis failed" in item for item in report.limitations)
    assert client.calls == 3


def test_ask_no_match_is_retrieval_only(tmp_path: Path) -> None:
    add_packet(tmp_path / "archive", "one")

    report = runner(tmp_path, FakeChatClient()).run(AskQuery(query="zzzznomatch"))

    assert report.status == "retrieval_only"
    assert report.citations == ()
    assert any("No archived source matched" in item for item in report.limitations)


def test_render_ask_note_contains_required_sections(tmp_path: Path) -> None:
    add_packet(tmp_path / "archive", "one")
    report = runner(tmp_path, FakeChatClient("the answer")).run(AskQuery(query="trading bot"))

    document = render_ask_note(report)

    assert document.path.endswith(".md")
    assert "schema_version: answer/v1" in document.content
    assert "provenance: agent_inference" in document.content
    assert "> [!warning] Machine inference" in document.content
    assert "decision-new" in document.content


def test_ask_to_dict_shape(tmp_path: Path) -> None:
    add_packet(tmp_path / "archive", "one")
    report = runner(tmp_path, FakeChatClient("the answer")).run(AskQuery(query="trading bot"))

    payload = ask_to_dict(report)

    assert payload["schema_version"] == "answer/v1"
    assert payload["status"] == "ok"
    assert payload["grounded"] is True
    assert isinstance(payload["citations"], list)
    assert isinstance(payload["citations"][0], dict)
    assert payload["citations"][0]["rank"] == 1
    assert isinstance(payload["limitations"], list)
