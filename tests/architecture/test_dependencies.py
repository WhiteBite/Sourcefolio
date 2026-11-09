from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[2] / "src" / "sourcefolio"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_does_not_depend_on_outer_layers() -> None:
    for path in (PACKAGE / "domain").rglob("*.py"):
        for module in imported_modules(path):
            assert not module.startswith("sourcefolio.application"), path
            assert not module.startswith("sourcefolio.adapters"), path


def test_application_does_not_depend_on_adapters() -> None:
    for path in (PACKAGE / "application").rglob("*.py"):
        for module in imported_modules(path):
            assert not module.startswith("sourcefolio.adapters"), path
