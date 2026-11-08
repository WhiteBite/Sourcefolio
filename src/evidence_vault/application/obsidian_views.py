from __future__ import annotations

import html
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

import yaml

from evidence_vault.application.extraction_ports import ExtractionRepository
from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.projection_ports import ProjectionStore
from evidence_vault.application.projection_types import GeneratedDocument


def _md(value: object) -> str:
    text = html.escape(" ".join(str(value).split()), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, f"\\{character}")
    return text


class ObsidianOverviewProjection:
    NAMESPACE = "overview"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        store: ProjectionStore,
        extractions: ExtractionRepository | None = None,
        latest_limit: int = 50,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if latest_limit < 1:
            raise ValueError("latest_limit must be at least 1")
        self._packets = packets
        self._store = store
        self._extractions = extractions
        self._latest_limit = latest_limit
        self._clock = clock or (lambda: datetime.now(UTC))

    def rebuild(self) -> int:
        packets = tuple(latest_capture_per_source(self._packets.iter_packets()).values())
        status_counts: Counter[str] = Counter()
        kind_counts: Counter[str] = Counter()
        collection_counts: Counter[str] = Counter()
        collection_sources: dict[str, set[str]] = defaultdict(set)
        extraction_counts: Counter[str] = Counter()
        derived_by_capture: Counter[str] = Counter()
        captures_with_text: set[str] = set()

        for packet in packets:
            status_counts[packet.status.value] += 1
            kind_counts[packet.source.kind.value] += 1
            for collection in packet.collections:
                collection_counts[collection.name] += 1
                collection_sources[collection.name].add(packet.source_id)
            records = tuple(
                self._extractions.iter_for_capture(packet.capture_id)
                if self._extractions is not None
                else ()
            )
            for record in records:
                extraction_counts[record.request.kind.value] += 1
                derived_by_capture[packet.capture_id] += 1
                if record.text.strip():
                    captures_with_text.add(packet.capture_id)

        generated_at = self._clock()
        properties = {
            "schema_version": "obsidian-overview/v1",
            "generated": True,
            "generator": "evidence-vault",
            "generator_version": "1",
            "generated_at": generated_at.isoformat(),
            "capture_count": len(packets),
            "source_count": len({packet.source_id for packet in packets}),
            "derived_extraction_count": sum(extraction_counts.values()),
            "captures_with_derived_text": len(captures_with_text),
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
            "# Evidence Vault overview",
            "",
            "> [!note] Generated view",
            "> Rebuildable from source packets and derived records; human notes are "
            "outside this namespace.",
            "",
            "## Coverage",
            "",
            "| Captures | Unique sources | Derived records | Captures with derived text |",
            "|---:|---:|---:|---:|",
            (
                f"| {len(packets)} | {len({packet.source_id for packet in packets})} "
                f"| {sum(extraction_counts.values())} | {len(captures_with_text)} |"
            ),
            "",
            "## Capture status",
            "",
            "| Status | Count |",
            "|---|---:|",
        ]
        lines.extend(
            f"| {_md(status)} | {count} |" for status, count in sorted(status_counts.items())
        )
        lines.extend(["", "## Source kinds", "", "| Kind | Count |", "|---|---:|"])
        lines.extend(f"| {_md(kind)} | {count} |" for kind, count in sorted(kind_counts.items()))
        lines.extend(
            [
                "",
                "## Derived extraction kinds",
                "",
                "| Kind | Records |",
                "|---|---:|",
            ]
        )
        if extraction_counts:
            lines.extend(
                f"| {_md(kind)} | {count} |" for kind, count in sorted(extraction_counts.items())
            )
        else:
            lines.append("| — | 0 |")
        lines.extend(
            [
                "",
                "## Collections",
                "",
                "| Collection | Captures | Unique sources |",
                "|---|---:|---:|",
            ]
        )
        if collection_counts:
            lines.extend(
                f"| {_md(name)} | {count} | {len(collection_sources[name])} |"
                for name, count in sorted(
                    collection_counts.items(), key=lambda item: item[0].casefold()
                )
            )
        else:
            lines.append("| — | 0 | 0 |")

        latest = sorted(
            packets,
            key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id),
        )
        lines.extend(
            [
                "",
                f"## Latest captures (up to {self._latest_limit})",
                "",
                "| Captured | Kind | Status | Collections | Derived | Card |",
                "|---|---|---|---|---:|---|",
            ]
        )
        for packet in latest[: self._latest_limit]:
            title = packet.title or packet.source.external_id or packet.source_id
            card = f"../source-cards/{packet.source_id}/{packet.capture_id}.md"
            collections = ", ".join(collection.name for collection in packet.collections)
            lines.append(
                f"| {packet.captured_at.isoformat()} | {_md(packet.source.kind.value)} "
                f"| {_md(packet.status.value)} | {_md(collections or '—')} "
                f"| {derived_by_capture[packet.capture_id]} "
                f"| [{_md(title)}]({card}) |"
            )
        if len(latest) > self._latest_limit:
            lines.extend(
                [
                    "",
                    f"Showing {self._latest_limit} of {len(latest)} captures; "
                    "use source cards for the complete archive.",
                ]
            )
        lines.append("")
        return self._store.replace_namespace(
            self.NAMESPACE,
            [GeneratedDocument(path="index.md", content="\n".join(lines))],
        )
