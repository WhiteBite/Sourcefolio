from __future__ import annotations

from dataclasses import dataclass

from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.ports import SourcePacketRepository
from sourcefolio.application.projection_ports import HumanNoteStore
from sourcefolio.application.triage import render_idea_note

_CLEARABLE = ("my_title", "why_saved")


@dataclass(frozen=True)
class NoteResult:
    note: str
    created: bool
    applied: dict[str, object | None]


class NoteSetter:
    def __init__(self, *, packets: SourcePacketRepository, notes: HumanNoteStore) -> None:
        self._packets = packets
        self._notes = notes

    def set(
        self,
        source_id: str,
        *,
        my_title: str | None = None,
        why_saved: str | None = None,
        status: str | None = None,
        clear: tuple[str, ...] = (),
    ) -> NoteResult | None:
        updates: dict[str, object | None] = {}
        if my_title is not None:
            updates["my_title"] = my_title
        if why_saved is not None:
            updates["why_saved"] = why_saved
        if status is not None:
            updates["status"] = status
        for field in clear:
            if field not in _CLEARABLE:
                raise ValueError(f"annotation field cannot be cleared: {field}")
            updates[field] = None
        if not updates:
            raise ValueError("nothing to set: pass --my-title, --why, --status or --clear")
        latest = latest_capture_per_source(self._packets.iter_packets())
        packet = latest.get(source_id)
        if packet is None:
            return None
        path = f"{source_id}.md"
        created = self._notes.create_if_absent(path, render_idea_note(packet))
        if not self._notes.update_properties(path, updates):
            raise ValueError(f"idea note is unreadable: {path}")
        return NoteResult(note=path, created=created, applied=dict(updates))
