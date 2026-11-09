from __future__ import annotations

from sourcefolio.application.entity_extraction import find_hashtags, find_repositories


def test_find_repositories_normalizes_github_and_gitlab() -> None:
    text = (
        "see https://github.com/Owner/Repo and gitlab.com/group/project.git "
        "and www.github.com/a/b/tree/main"
    )

    assert find_repositories(text) == (
        "https://github.com/Owner/Repo",
        "https://gitlab.com/group/project",
        "https://github.com/a/b",
    )


def test_find_repositories_dedupes_and_ignores_other_hosts() -> None:
    text = "github.com/a/b github.com/a/b https://example.com/a/b"

    assert find_repositories(text) == ("https://github.com/a/b",)


def test_find_repositories_requires_owner_and_repo() -> None:
    assert find_repositories("github.com/onlyowner") == ()


def test_find_hashtags_extracts_unicode_and_dedupes() -> None:
    text = "текст #нейронки #AI #нейронки"

    assert find_hashtags(text) == ("нейронки", "AI")


def test_find_hashtags_ignores_word_embedded_hash() -> None:
    assert find_hashtags("color#ff0000 and #real") == ("real",)
