from __future__ import annotations

from sourcefolio.application.slugs import slugify, unique_slugs


def test_slugify_transliterates_cyrillic_and_normalizes() -> None:
    assert slugify("Нейронки") == "neyronki"
    assert slugify("My Ideas 2026") == "my-ideas-2026"
    assert slugify("  Mixed CASE  ") == "mixed-case"


def test_slugify_falls_back_to_hash_for_empty_slug() -> None:
    slug = slugify("!!!")

    assert slug.startswith("collection-")
    assert len(slug) == len("collection-") + 12


def test_unique_slugs_dedupes_with_hash_suffix() -> None:
    slugs = unique_slugs(["Идеи", "идеи"])

    assert slugs["Идеи"] == "idei"
    assert slugs["идеи"].startswith("idei-")
    assert slugs["идеи"] != slugs["Идеи"]


def test_unique_slugs_sorted_by_casefold() -> None:
    slugs = unique_slugs(["b", "A", "c"])

    assert list(slugs) == ["A", "b", "c"]
