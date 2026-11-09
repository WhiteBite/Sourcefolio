from __future__ import annotations

from datetime import UTC, datetime

from sourcefolio.application.markdown import (
    card_link,
    escape,
    generated_callout,
    generated_frontmatter,
    mermaid_id,
    mermaid_label,
    note_tags,
    wikilink_alias,
)
from sourcefolio.domain.common import ActorKind, Provenance
from sourcefolio.domain.source import (
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

    link = card_link("_generated/sourcefolio", packet)

    assert link == (
        f"[[_generated/sourcefolio/source-cards/{packet.source_id}/{packet.capture_id}|My Title]]"
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


def test_mermaid_id_is_sanitized() -> None:
    node_id = mermaid_id("repo", "owner/name.py")

    assert node_id.startswith("repo_")
    for character in ("/", " ", ".", ":", "-"):
        assert character not in node_id


def test_mermaid_id_is_deterministic_and_distinct() -> None:
    assert mermaid_id("repo", "owner/name.py") == mermaid_id("repo", "owner/name.py")
    assert mermaid_id("repo", "owner/name.py") != mermaid_id("repo", "owner/other.py")
    assert mermaid_id("repo", "owner/name.py") != mermaid_id("entity", "owner/name.py")


def test_mermaid_label_strips_breaking_characters() -> None:
    label = mermaid_label('a"b]c\nd')

    assert '"' not in label
    assert "]" not in label
    assert "\n" not in label


def test_mermaid_label_neutralizes_mermaid_significant_characters() -> None:
    label = mermaid_label('a;b`c#d%e<f>g(h)i"j]k\nl')

    for character in (";", "`", "#", "%", "<", ">", "(", ")", '"', "]", "\n"):
        assert character not in label


def test_mermaid_label_neutralizes_remaining_significant_characters() -> None:
    label = mermaid_label('a{b}c&d|e\\f;g`h#i%j<k>l(m)n"o]p\nq')

    for character in (
        "{",
        "}",
        "&",
        "|",
        "\\",
        ";",
        "`",
        "#",
        "%",
        "<",
        ">",
        "(",
        ")",
        '"',
        "]",
        "\n",
    ):
        assert character not in label


def test_note_tags_returns_kind_scoped_tags() -> None:
    assert note_tags("repo") == ["sourcefolio", "sourcefolio/repo"]
