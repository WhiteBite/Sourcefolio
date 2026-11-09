from __future__ import annotations

from pathlib import Path

from evidence_vault.adapters.filesystem.repo_reader import FileSystemRepoReader

_MAX_README_BYTES = 256 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024


def test_read_readme_returns_text(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_bytes(b"# Title\n\nbody")

    assert FileSystemRepoReader().read_readme(str(tmp_path)) == "# Title\n\nbody"


def test_read_readme_prefers_markdown(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("markdown", encoding="utf-8")
    (tmp_path / "README.rst").write_text("rst", encoding="utf-8")

    assert FileSystemRepoReader().read_readme(str(tmp_path)) == "markdown"


def test_read_readme_falls_back_to_rst(tmp_path: Path) -> None:
    (tmp_path / "README.rst").write_text("rst", encoding="utf-8")

    assert FileSystemRepoReader().read_readme(str(tmp_path)) == "rst"


def test_read_readme_truncates_oversized(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_bytes(b"a" * (_MAX_README_BYTES + 100))

    text = FileSystemRepoReader().read_readme(str(tmp_path))

    assert text is not None
    assert len(text) == _MAX_README_BYTES


def test_read_readme_replaces_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_bytes(b"ok \xff\xfe end")

    text = FileSystemRepoReader().read_readme(str(tmp_path))

    assert text is not None
    assert text.startswith("ok ")
    assert text.endswith(" end")


def test_read_manifest_parses_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pydantic>=2"]\n',
        encoding="utf-8",
    )

    manifest = FileSystemRepoReader().read_manifest(str(tmp_path))

    assert manifest is not None
    assert manifest["project"] == {"name": "demo", "dependencies": ["pydantic>=2"]}


def test_read_manifest_parses_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "dependencies": {"react": "^18"}}',
        encoding="utf-8",
    )

    manifest = FileSystemRepoReader().read_manifest(str(tmp_path))

    assert manifest is not None
    assert manifest["dependencies"] == {"react": "^18"}


def test_read_manifest_prefers_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "toml"\n', encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "json"}', encoding="utf-8")

    manifest = FileSystemRepoReader().read_manifest(str(tmp_path))

    assert manifest is not None
    assert manifest["project"] == {"name": "toml"}


def test_missing_directory_returns_none(tmp_path: Path) -> None:
    reader = FileSystemRepoReader()
    missing = str(tmp_path / "absent")

    assert reader.read_readme(missing) is None
    assert reader.read_manifest(missing) is None


def test_oversized_manifest_returns_none(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_bytes(b"#" + b"a" * _MAX_MANIFEST_BYTES)

    assert FileSystemRepoReader().read_manifest(str(tmp_path)) is None


def test_invalid_manifest_returns_none(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("not = = toml", encoding="utf-8")

    assert FileSystemRepoReader().read_manifest(str(tmp_path)) is None


def test_invalid_package_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")

    assert FileSystemRepoReader().read_manifest(str(tmp_path)) is None


def test_readme_directory_entry_returns_none(tmp_path: Path) -> None:
    (tmp_path / "README.md").mkdir()

    assert FileSystemRepoReader().read_readme(str(tmp_path)) is None
