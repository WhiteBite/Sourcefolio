from __future__ import annotations

import html
from collections.abc import Mapping

import yaml

from evidence_vault.domain.source import SourcePacket


def escape(value: object, *, table: bool = False) -> str:
    text = html.escape(" ".join(str(value).split()), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(character, f"\\{character}")
    if table:
        text = text.replace("|", "\\|")
    return text


def wikilink_alias(value: str) -> str:
    cleaned = " ".join(value.split())
    for character in ("[", "]", "|", "#", "^"):
        cleaned = cleaned.replace(character, " ")
    return " ".join(cleaned.split()) or "source"


def card_link(generated_prefix: str, packet: SourcePacket) -> str:
    prefix = generated_prefix.strip("/")
    target = f"{prefix}/source-cards/{packet.source_id}/{packet.capture_id}"
    alias = wikilink_alias(packet.title or packet.source.external_id or packet.source_id)
    return f"[[{target}|{alias}]]"


def generated_frontmatter(
    extra: Mapping[str, object],
    *,
    schema_version: str,
    generator_version: str,
) -> str:
    properties = {
        "schema_version": schema_version,
        "generated": True,
        "generator": "evidence-vault",
        "generator_version": generator_version,
        **extra,
    }
    return yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()


def generated_callout(text: str) -> list[str]:
    return ["> [!note] Generated view", f"> {text}"]
