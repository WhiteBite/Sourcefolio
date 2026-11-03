from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from evidence_vault.adapters.filesystem.atomic import write_immutable_bytes
from evidence_vault.domain.common import canonical_json
from evidence_vault.domain.source import CaptureId, SourceId, SourcePacket


class FileSystemSourcePacketRepository:
    def __init__(self, archive_root: Path) -> None:
        self._root = archive_root

    def _path(self, source_id: SourceId, capture_id: CaptureId) -> Path:
        return self._root / "sources" / source_id / f"{capture_id}.json"

    def save(self, packet: SourcePacket) -> bool:
        # Revalidate at the trust boundary: nested JSON metadata is not deeply frozen.
        packet = SourcePacket.model_validate(packet.model_dump())
        path = self._path(packet.source_id, packet.capture_id)
        data = (canonical_json(packet) + "\n").encode("utf-8")
        return write_immutable_bytes(path, data)

    def get(self, source_id: SourceId, capture_id: CaptureId) -> SourcePacket | None:
        path = self._path(source_id, capture_id)
        if not path.exists():
            return None
        packet = SourcePacket.model_validate_json(path.read_bytes())
        if packet.source_id != source_id or packet.capture_id != capture_id:
            raise ValueError("source packet path does not match its identifiers")
        return packet

    def iter_packets(self) -> Iterator[SourcePacket]:
        source_root = self._root / "sources"
        if not source_root.exists():
            return
        for path in sorted(source_root.glob("src_*/cap_*.json")):
            packet = SourcePacket.model_validate_json(path.read_bytes())
            if path != self._path(packet.source_id, packet.capture_id):
                raise ValueError("source packet path does not match its identifiers")
            yield packet
