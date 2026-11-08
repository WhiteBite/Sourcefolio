from __future__ import annotations

from dataclasses import dataclass

import yaml

from evidence_vault.application.latest_captures import latest_capture_per_source
from evidence_vault.application.ports import SourcePacketRepository
from evidence_vault.application.projection_ports import HumanNoteStore
from evidence_vault.domain.source import SourcePacket


@dataclass(frozen=True)
class IdeaScaffoldResult:
    created: int
    skipped: int
    created_paths: tuple[str, ...]


class IdeaScaffolder:
    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        notes: HumanNoteStore,
    ) -> None:
        self._packets = packets
        self._notes = notes

    def scaffold(self) -> IdeaScaffoldResult:
        latest = latest_capture_per_source(self._packets.iter_packets())

        created_paths: list[str] = []
        skipped = 0
        for source_id in sorted(latest):
            path = f"{source_id}.md"
            if self._notes.create_if_absent(path, self._render(latest[source_id])):
                created_paths.append(path)
            else:
                skipped += 1
        return IdeaScaffoldResult(
            created=len(created_paths),
            skipped=skipped,
            created_paths=tuple(created_paths),
        )

    @staticmethod
    def _render(packet: SourcePacket) -> str:
        title = packet.title or packet.source.external_id or packet.source_id
        collections = sorted({collection.name for collection in packet.collections})
        properties = {
            "status": "inbox",
            "source_id": packet.source_id,
            "title": title,
            "collections": collections,
        }
        frontmatter = yaml.safe_dump(properties, allow_unicode=True, sort_keys=False).strip()
        lines = ["---", frontmatter, "---", "", "## Заметки", ""]
        return "\n".join(lines)
