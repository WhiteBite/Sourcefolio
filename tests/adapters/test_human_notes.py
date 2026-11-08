from __future__ import annotations

from pathlib import Path

import pytest

from evidence_vault.adapters.filesystem.human_notes import FileSystemHumanNoteStore


def test_iter_notes_yields_sorted_posix_paths(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    (root / "projects").mkdir(parents=True)
    (root / "decisions").mkdir()
    (root / "projects" / "b.md").write_text("bee", encoding="utf-8")
    (root / "decisions" / "a.md").write_text("aye", encoding="utf-8")
    (root / "top.md").write_text("top", encoding="utf-8")
    (root / "ignore.txt").write_text("nope", encoding="utf-8")

    notes = list(FileSystemHumanNoteStore(root).iter_notes())

    assert notes == [
        ("decisions/a.md", "aye"),
        ("projects/b.md", "bee"),
        ("top.md", "top"),
    ]


def test_iter_notes_empty_when_root_missing(tmp_path: Path) -> None:
    assert list(FileSystemHumanNoteStore(tmp_path / "absent").iter_notes()) == []


def test_read_returns_content(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    (root / "note.md").write_text("hello", encoding="utf-8")

    assert FileSystemHumanNoteStore(root).read("note.md") == "hello"


def test_read_returns_none_for_missing(tmp_path: Path) -> None:
    assert FileSystemHumanNoteStore(tmp_path).read("missing.md") is None


def test_read_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FileSystemHumanNoteStore(tmp_path).read("../escape.md")


def test_iter_notes_skips_undecodable_file(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    (root / "good.md").write_text("ok", encoding="utf-8")
    (root / "bad.md").write_bytes("привет".encode("cp1251"))

    store = FileSystemHumanNoteStore(root)

    with pytest.warns(UserWarning):
        notes = list(store.iter_notes())

    assert notes == [("good.md", "ok")]
    assert store.read("bad.md") is None


def test_create_if_absent_never_overwrites(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    store = FileSystemHumanNoteStore(root)

    assert store.create_if_absent("projects/p.md", "first") is True
    assert store.create_if_absent("projects/p.md", "second") is False
    assert (root / "projects" / "p.md").read_bytes() == b"first"
