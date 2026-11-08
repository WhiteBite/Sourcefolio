from __future__ import annotations

from collections.abc import Iterable

from evidence_vault.domain.source import SourceId, SourcePacket


def latest_capture_per_source(packets: Iterable[SourcePacket]) -> dict[SourceId, SourcePacket]:
    latest: dict[SourceId, SourcePacket] = {}
    for packet in packets:
        current = latest.get(packet.source_id)
        if current is None or (packet.captured_at, packet.capture_id) > (
            current.captured_at,
            current.capture_id,
        ):
            latest[packet.source_id] = packet
    return latest
