from __future__ import annotations

import re

import yaml

from sourcefolio.application.projection_types import GeneratedDocument
from sourcefolio.application.registry_queue import (
    ProjectQueue,
    QueueCandidate,
    QueueMention,
    RegistryQueue,
)
from sourcefolio.application.registry_views import RegistryViewProjection
from sourcefolio.domain.registry import CandidateKind, DecisionStatus, FitLevel


def make_mention(
    *,
    source_id: str = "src_1",
    capture_id: str = "cap_1",
    title: str = "Source",
    quote: str = "Candidate quote text",
    fit: FitLevel = FitLevel.HIGH,
) -> QueueMention:
    return QueueMention(
        source_id=source_id,
        capture_id=capture_id,
        url=f"https://example.test/{source_id}",
        title=title,
        quote=quote,
        field="description",
        fit=fit,
    )


def make_candidate(
    *,
    candidate_id: str = "cand_1",
    title: str = "Tool",
    kind: CandidateKind = CandidateKind.TOOL,
    fit: FitLevel = FitLevel.HIGH,
    mention_count: int = 1,
    outcome: str = DecisionStatus.INBOX.value,
    mentions: tuple[QueueMention, ...] | None = None,
) -> QueueCandidate:
    resolved = (make_mention(),) if mentions is None else mentions
    return QueueCandidate(
        candidate_id=candidate_id,
        title=title,
        kind=kind,
        key="owner/repo",
        fit=fit,
        mention_count=mention_count,
        source_count=1,
        score=1,
        outcome=outcome,
        mentions=resolved,
    )


def make_queue(
    *projects: ProjectQueue,
    snapshot_id: str | None = "snap_1",
    generated_at: str | None = None,
) -> RegistryQueue:
    return RegistryQueue(
        snapshot_id=snapshot_id,
        generated_at=generated_at,
        projects=projects,
    )


def properties(document: GeneratedDocument) -> dict[str, object]:
    return yaml.safe_load(document.content.split("---", 2)[1])


def test_render_yields_index_and_one_project_document() -> None:
    queue = make_queue(
        ProjectQueue(project_id="alpha", name="Alpha", candidates=(make_candidate(),)),
        ProjectQueue(project_id="midas", name="Midas AI", candidates=()),
    )

    documents = RegistryViewProjection().render(queue)

    assert [document.path for document in documents] == [
        "index.md",
        "alpha.md",
        "midas-ai.md",
    ]
    assert RegistryViewProjection().render(queue) == documents


def test_frontmatter_keys_and_schema_version() -> None:
    queue = make_queue(
        ProjectQueue(project_id="alpha", name="Alpha", candidates=(make_candidate(),)),
        generated_at="2026-11-03T12:00:00+00:00",
    )

    documents = RegistryViewProjection().render(queue)
    index = properties(documents[0])
    project = properties(documents[1])

    assert index["schema_version"] == "registry-view/v1"
    assert index["generator_version"] == "1"
    assert index["provenance"] == "agent_inference"
    assert index["snapshot_id"] == "snap_1"
    assert index["project_count"] == 1
    assert index["candidate_count"] == 1
    assert index["accepted_count"] == 0
    assert index["generated_at"] == "2026-11-03T12:00:00+00:00"
    assert project["schema_version"] == "registry-view/v1"
    assert project["provenance"] == "agent_inference"
    assert project["snapshot_id"] == "snap_1"
    assert project["project_id"] == "alpha"
    assert project["project_name"] == "Alpha"
    assert project["candidate_count"] == 1
    assert project["accepted_count"] == 0


def test_candidate_rows_keep_queue_order() -> None:
    queue = make_queue(
        ProjectQueue(
            project_id="alpha",
            name="Alpha",
            candidates=(
                make_candidate(candidate_id="cand_z", title="Zeta"),
                make_candidate(candidate_id="cand_a", title="Alpha tool"),
            ),
        )
    )

    content = RegistryViewProjection().render(queue)[1].content

    assert content.index("### Zeta") < content.index("### Alpha tool")


def test_quotes_are_verbatim_and_link_to_source_card() -> None:
    quote = "Needle RAG sentence stays verbatim"
    mentions = (
        make_mention(source_id="src_9", capture_id="cap_9", quote=quote),
        make_mention(source_id="src_8", capture_id="cap_8", quote="Second quote"),
        make_mention(source_id="src_7", capture_id="cap_7", quote="Third quote"),
        make_mention(source_id="src_6", capture_id="cap_6", quote="Fourth quote is hidden"),
    )
    queue = make_queue(
        ProjectQueue(
            project_id="alpha",
            name="Alpha",
            candidates=(make_candidate(mention_count=4, mentions=mentions),),
        )
    )

    content = RegistryViewProjection().render(queue)[1].content

    assert quote in content
    assert "sourcefolio/source-cards/src_9/cap_9" in content
    assert "Fourth quote is hidden" not in content


def test_accepted_candidate_is_marked() -> None:
    queue = make_queue(
        ProjectQueue(
            project_id="alpha",
            name="Alpha",
            candidates=(make_candidate(outcome=DecisionStatus.ACCEPTED.value),),
        )
    )

    documents = RegistryViewProjection().render(queue)

    assert "Outcome: **accepted**" in documents[1].content
    assert "accepted_count: 1" in documents[0].content


def test_empty_queue_yields_single_index_with_zero_counts() -> None:
    documents = RegistryViewProjection().render(
        RegistryQueue(snapshot_id=None, generated_at=None, projects=())
    )

    assert len(documents) == 1
    assert documents[0].path == "index.md"
    index = properties(documents[0])
    assert index["project_count"] == 0
    assert index["candidate_count"] == 0
    assert index["accepted_count"] == 0
    assert index["snapshot_id"] is None
    assert "generated_at" not in index


def test_cyrillic_project_name_gets_ascii_slug() -> None:
    queue = make_queue(
        ProjectQueue(project_id="neyronki", name="Нейронки", candidates=(make_candidate(),))
    )

    documents = RegistryViewProjection().render(queue)
    path = documents[1].path

    assert path == "neyronki.md"
    assert path.isascii()
    assert re.fullmatch(r"[a-z0-9-]+\.md", path)
    assert "Нейронки" in documents[1].content
