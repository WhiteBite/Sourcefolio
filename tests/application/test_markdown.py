from __future__ import annotations

from datetime import UTC, datetime

from evidence_vault.application.markdown import (
    card_link,
    escape,
    generated_callout,
    generated_frontmatter,
    wikilink_alias,
)
from evidence_vault.domain.common import ActorKind, Provenance
from evidence_vault.domain.source import (
    ArtifactRef,
    ArtifactRole,
    SourceIdentity,
    SourceKind,
    SourcePacket,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _packet(*, title: str | None, external_id: str | None = "ext-1") -> SourcePacket:
    return SourcePacket.create(
        source=SourceIdentity(
            platform="tiktok",
            kind=SourceKind.VIDEO,
            external_id=external_id,
            canonical_url="https://example.test/v/1",
        ),
        captured_at=NOW,
        collector=Provenance(actor_kind=ActorKind.TOOL, actor_id="test", method="test"),
        artifacts=(
            ArtifactRef(
                artifact_id="art_" + "0" * 64,
                role=ArtifactRole.CAPTION_TEXT,
                media_type="text/plain",
                size_bytes=1,
                sha256="0" * 64,
            ),
        ),
        title=title,
    )


def test_escape_collapses_whitespace_and_escapes_markdown() -> None:
    assert escape("  a   b  ") == "a b"
    assert escape("a*b_c[d]") == "a\\*b\\_c\\[d\\]"
    assert escape("a|b") == "a|b"
    assert escape("a|b", table=True) == "a\\|b"


def test_escape_accepts_non_string_values() -> None:
    assert escape(42) == "42"


def test_wikilink_alias_strips_reserved_characters() -> None:
    assert wikilink_alias("a [b] | c #d ^e") == "a b c d e"
    assert wikilink_alias("   ") == "source"


def test_card_link_uses_prefix_and_title_alias() -> None:
    packet = _packet(title="My [Title]")

    link = card_link("_generated/evidence-vault", packet)

    assert link == (
        f"[[_generated/evidence-vault/source-cards/{packet.source_id}/{packet.capture_id}"
        "|My Title]]"
    )


def test_card_link_falls_back_to_external_id() -> None:
    packet = _packet(title=None, external_id="ext-9")

    assert card_link("prefix", packet).endswith("|ext-9]]")


def test_generated_frontmatter_orders_generated_keys_first() -> None:
    frontmatter = generated_frontmatter(
        {"collection": "Нейронки"},
        schema_version="collection-index/v1",
        generator_version="1",
    )

    keys = [line.split(":", 1)[0] for line in frontmatter.splitlines()]
    assert keys == [
        "schema_version",
        "generated",
        "generator",
        "generator_version",
        "collection",
    ]


def test_generated_callout_returns_note_lines() -> None:
    assert generated_callout("Rebuildable.") == [
        "> [!note] Generated view",
        "> Rebuildable.",
    ]
