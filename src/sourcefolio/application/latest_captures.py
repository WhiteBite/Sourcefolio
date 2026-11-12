from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sourcefolio.domain.source import SourceId, SourcePacket


def _rank(packet: SourcePacket) -> tuple[datetime, int, str]:
    return (packet.captured_at, len(packet.artifacts), packet.capture_id)


def latest_capture_per_source(packets: Iterable[SourcePacket]) -> dict[SourceId, SourcePacket]:
    latest: dict[SourceId, SourcePacket] = {}
    for packet in packets:
        current = latest.get(packet.source_id)
        if current is None or _rank(packet) > _rank(current):
            latest[packet.source_id] = packet
    return latest
