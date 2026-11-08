from __future__ import annotations

import pytest

from evidence_vault.application.frontmatter import Frontmatter, note_body, parse_frontmatter


def test_valid_frontmatter_returns_properties() -> None:
    result = parse_frontmatter("---\ntitle: Hello\nstatus: inbox\n---\nbody")
    assert result.properties == {"title": "Hello", "status": "inbox"}
    assert result.warning is None


def test_malformed_yaml_returns_empty_with_warning() -> None:
    content = '---\ntitle: "unterminated\n---\nbody'
    with pytest.warns(UserWarning):
        result = parse_frontmatter(content)
    assert result.properties == {}
    assert result.warning is not None


def test_value_containing_fence_is_not_truncated() -> None:
    result = parse_frontmatter("---\ntitle: A --- B\n---\nx")
    assert result.properties == {"title": "A --- B"}
    assert result.warning is None


def test_non_mapping_frontmatter_warns() -> None:
    with pytest.warns(UserWarning):
        result = parse_frontmatter("---\n- a\n- b\n---\nbody")
    assert result.properties == {}
    assert result.warning is not None


def test_note_body_keeps_inner_fence() -> None:
    content = "---\ntitle: t\n---\nline1\n---\nline2"
    assert note_body(content) == "line1\n---\nline2"


def test_no_frontmatter_returns_content_unchanged() -> None:
    content = "just a body\nwith --- inside"
    assert note_body(content) == content
    assert parse_frontmatter(content) == Frontmatter({}, None)


def test_crlf_fences_are_supported() -> None:
    result = parse_frontmatter("---\r\ntitle: Hello\r\n---\r\nbody")
    assert result.properties == {"title": "Hello"}
    assert note_body("---\r\ntitle: Hello\r\n---\r\nbody") == "body"
