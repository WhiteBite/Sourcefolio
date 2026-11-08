from __future__ import annotations

import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass

import yaml

_FRONTMATTER = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class Frontmatter:
    properties: Mapping[str, object]
    warning: str | None


def parse_frontmatter(content: str) -> Frontmatter:
    match = _FRONTMATTER.match(content)
    if match is None:
        return Frontmatter({}, None)
    try:
        loaded = yaml.safe_load(match.group("body"))
    except yaml.YAMLError as exc:
        warning = f"invalid YAML frontmatter: {exc}"
        warnings.warn(warning, stacklevel=2)
        return Frontmatter({}, warning)
    if not isinstance(loaded, dict):
        warning = "frontmatter is not a mapping"
        warnings.warn(warning, stacklevel=2)
        return Frontmatter({}, warning)
    return Frontmatter({str(key): value for key, value in loaded.items()}, None)


def note_body(content: str) -> str:
    match = _FRONTMATTER.match(content)
    if match is None:
        return content
    return content[match.end() :]
