from __future__ import annotations

import html
from collections.abc import Mapping
from urllib.parse import urlsplit

import yaml
from pydantic import JsonValue

from evidence_vault.application.extraction_ports import ExtractionRepository
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.ports import ArtifactLocator, SourcePacketRepository
from evidence_vault.application.projection_ports import ProjectionStore
from evidence_vault.application.projection_types import GeneratedDocument
from evidence_vault.application.saved_index import (
    saved_comments,
    saved_index,
    saved_int,
    saved_str,
)
from evidence_vault.domain.source import ArtifactRef, SourcePacket

_MAX_COMMENTS = 10


def _markdown(value: str, *, table: bool = False) -> str:
    text = html.escape(" ".join(value.split()), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(character, f"\\{character}")
    if table:
        text = text.replace("|", "\\|")
    return text


def _link(url: str, label: str) -> str:
    if urlsplit(url).scheme not in {"http", "https", "file"}:
        return f"`{_markdown(url)}`"
    destination = url.replace(" ", "%20").replace("<", "%3C").replace(">", "%3E")
    return f"[{_markdown(label)}](<{destination}>)"


def _artifact_row(artifact: ArtifactRef, artifacts: ArtifactLocator) -> str:
    uri = artifacts.uri(artifact.artifact_id)
    return "| {role} | {media_type} | {size} | `{sha}` | {link} |".format(
        role=_markdown(artifact.role.value, table=True),
        media_type=_markdown(artifact.media_type, table=True),
        size=artifact.size_bytes,
        sha=artifact.sha256,
        link=_link(uri, "open"),
    )


def _comment_likes(comment: Mapping[str, JsonValue]) -> int:
    value = saved_int(comment, "likes")
    return value if value is not None else -1


def _top_comments(
    comments: tuple[Mapping[str, JsonValue], ...],
) -> list[Mapping[str, JsonValue]]:
    indexed = list(enumerate(comments))
    indexed.sort(key=lambda pair: (-_comment_likes(pair[1]), pair[0]))
    return [comment for _, comment in indexed[:_MAX_COMMENTS]]


class SourceCardProjection:
    NAMESPACE = "source-cards"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactLocator,
        store: ProjectionStore,
        extractions: ExtractionRepository | None = None,
    ) -> None:
        self._packets = packets
        self._artifacts = artifacts
        self._store = store
        self._extractions = extractions

    def rebuild(self) -> int:
        latest = latest_capture_per_source(self._packets.iter_packets())
        return self._store.replace_namespace(
            self.NAMESPACE,
            (self.render(packet) for packet in latest.values()),
        )

    def render(self, packet: SourcePacket) -> GeneratedDocument:
        records = tuple(
            self._extractions.iter_for_capture(packet.capture_id)
            if self._extractions is not None
            else ()
        )
        collections = [collection.name for collection in packet.collections]
        saved = saved_index(packet)
        author = saved_str(saved, "author")
        video_type = saved_str(saved, "type")
        post_created_at = saved_str(saved, "post_created_at")
        description = saved_str(saved, "desc")
        comments = saved_comments(saved)
        title = packet.title or (
            f"{packet.source.kind.value} {packet.source.external_id or packet.source_id[:12]}"
        )
        properties = {
            "schema_version": "source-card/v1",
            "title": title,
            "generated": True,
            "generator": "evidence-vault",
            "generator_version": "2",
            "source_id": packet.source_id,
            "capture_id": packet.capture_id,
            "platform": packet.source.platform,
            "kind": packet.source.kind.value,
            "external_id": packet.source.external_id,
            "canonical_url": packet.source.canonical_url,
            "captured_at": packet.captured_at.isoformat(),
            "status": packet.status.value,
            "collections": collections,
            "author": author,
            "video_type": video_type,
            "post_created_at": post_created_at,
            "artifact_count": len(packet.artifacts),
            "derived_extraction_count": len(records),
            "derived_extraction_kinds": sorted({record.request.kind.value for record in records}),
            "has_derived_text": any(bool(record.text.strip()) for record in records),
        }
        frontmatter = yaml.safe_dump(
            properties,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        lines = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {_markdown(title)}",
            "",
            "> [!note] Generated view",
            "> Rebuildable from immutable source packets. Edit human notes outside this namespace.",
            "",
            "## Source",
            "",
        ]
        if packet.source.canonical_url:
            lines.append(f"- URL: {_link(packet.source.canonical_url, 'open original')}")
        lines.extend(
            [
                f"- Source ID: `{packet.source_id}`",
                f"- Capture ID: `{packet.capture_id}`",
                f"- Captured: `{packet.captured_at.isoformat()}`",
                f"- Status: **{packet.status.value}**",
                f"- Collections: {_markdown(', '.join(collections)) if collections else '—'}",
            ]
        )
        if author is not None:
            lines.append(f"- Author: {_markdown(author)}")
        if video_type is not None:
            lines.append(f"- Type: {_markdown(video_type)}")
        if post_created_at is not None:
            lines.append(f"- Posted: `{_markdown(post_created_at)}`")
        lines.append("")
        if description is not None:
            lines.extend(["## Description", "", _markdown(description), ""])
        if comments:
            lines.extend(
                [
                    "## Comments",
                    "",
                    "| Author | Likes | Date | Text |",
                    "|---|---:|---|---|",
                ]
            )
            for comment in _top_comments(comments):
                likes = saved_int(comment, "likes")
                lines.append(
                    "| {author} | {likes} | {date} | {text} |".format(
                        author=_markdown(saved_str(comment, "author") or "—", table=True),
                        likes=likes if likes is not None else "—",
                        date=_markdown(saved_str(comment, "created_at") or "—", table=True),
                        text=_markdown(saved_str(comment, "text") or "—", table=True),
                    )
                )
            lines.append("")
        if packet.limitations:
            lines.extend(["## Limitations", ""])
            lines.extend(f"- {_markdown(item)}" for item in packet.limitations)
            lines.append("")
        lines.extend(
            [
                "## Artifacts",
                "",
                "| Role | Media type | Bytes | SHA-256 | Link |",
                "|---|---|---:|---|---|",
            ]
        )
        lines.extend(_artifact_row(artifact, self._artifacts) for artifact in packet.artifacts)
        lines.extend(["", "## Derived text", ""])
        if not records:
            lines.append("No OCR or ASR records are available for this capture.")
        for record in records:
            request = record.request
            heading = f"{_markdown(request.kind.value)} · `{_markdown(request.locator.canonical)}`"
            extractor = (
                f"{_markdown(request.extractor.actor_id)} / "
                f"{_markdown(request.extractor.version or 'unknown')}"
            )
            artifact_link = _link(
                self._artifacts.uri(request.artifact_id),
                "open artifact",
            )
            lines.extend(
                [
                    f"### {heading}",
                    "",
                    f"- Extraction ID: `{record.extraction_id}`",
                    f"- Request ID: `{record.request_id}`",
                    f"- Created: `{record.created_at.isoformat()}`",
                    f"- Extractor: `{extractor}`",
                    f"- Artifact: {artifact_link}",
                    f"- Excerpt: {_markdown(record.excerpt) if record.excerpt else '—'}",
                ]
            )
            if record.limitations:
                lines.append(f"- Limitations: {_markdown('; '.join(record.limitations))}")
            lines.append("")
        lines.extend(
            [
                "",
                "## Collector provenance",
                "",
                f"- Actor: `{packet.collector.actor_kind.value}:{packet.collector.actor_id}`",
                f"- Method: `{_markdown(packet.collector.method)}`",
                f"- Version: `{_markdown(packet.collector.version or 'unknown')}`",
                "",
            ]
        )
        return GeneratedDocument(
            path=f"{packet.source_id}/{packet.capture_id}.md",
            content="\n".join(lines),
        )
