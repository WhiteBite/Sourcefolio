from __future__ import annotations

import hashlib
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


def card_link_ids(generated_prefix: str, source_id: str, capture_id: str, alias: str) -> str:
    prefix = generated_prefix.strip("/")
    target = f"{prefix}/source-cards/{source_id}/{capture_id}"
    return f"[[{target}|{wikilink_alias(alias)}]]"


def card_link(generated_prefix: str, packet: SourcePacket) -> str:
    return card_link_ids(
        generated_prefix,
        packet.source_id,
        packet.capture_id,
        packet.title or packet.source.external_id or packet.source_id,
    )


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


def mermaid_id(kind: str, key: str) -> str:
    safe_kind = "".join(
        character if character.isascii() and (character.isalnum() or character == "_") else "_"
        for character in kind
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"{safe_kind}_{digest}"


def mermaid_label(value: str) -> str:
    cleaned = " ".join(value.split())
    for character in ('"', "[", "]"):
        cleaned = cleaned.replace(character, "")
    return cleaned


def note_tags(kind: str) -> list[str]:
    return ["evidence-vault", f"evidence-vault/{kind}"]
