from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pydantic import JsonValue

from sourcefolio.application.entity_extraction import find_hashtags
from sourcefolio.application.extraction_ports import ExtractionRepository
from sourcefolio.application.frontmatter import note_body, parse_frontmatter
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.ports import ArtifactLocator, SourcePacketRepository
from sourcefolio.application.projection_ports import HumanNoteReader, ProjectionStore
from sourcefolio.application.projection_types import GeneratedDocument
from sourcefolio.application.saved_index import saved_index, saved_int, saved_str
from sourcefolio.domain.extraction import ExtractionRecord
from sourcefolio.domain.source import ArtifactRef, CaptureStatus, SourcePacket

_SCHEMA_VERSION = "sourcefolio-ui/v1"
_GENERATOR_VERSION = "3"
_MAX_TITLE_CHARS = 80
_NOTE_PREVIEW_CHARS = 120
_MEDIA_NOT_REQUESTED = "media capture was not requested"
_NOTE_HEADING = "## Заметки"
_HASHTAG_TOKEN = re.compile(r"(?<!\w)#\w+", re.UNICODE)

_EMPTY_ANNOTATION: dict[str, str | None] = {
    "my_title": None,
    "why_saved": None,
    "status": None,
    "note_preview": None,
}


def _artifact_view(artifact: ArtifactRef, artifacts: ArtifactLocator) -> dict[str, object]:
    media_type = artifact.media_type
    return {
        "artifact_id": artifact.artifact_id,
        "role": artifact.role.value,
        "media_type": media_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "uri": artifacts.uri(artifact.artifact_id),
        "is_image": media_type.startswith("image/"),
        "is_video": media_type.startswith("video/"),
    }


def _derived_view(records: tuple[ExtractionRecord, ...]) -> dict[str, object]:
    excerpts: list[dict[str, object]] = []
    kinds: set[str] = set()
    has_text = False
    for record in records:
        kind = record.request.kind.value
        kinds.add(kind)
        if record.text.strip():
            has_text = True
        excerpts.append(
            {
                "kind": kind,
                "locator": record.request.locator.canonical,
                "extractor": (
                    f"{record.request.extractor.actor_id}/"
                    f"{record.request.extractor.version or 'unknown'}"
                ),
                "text": record.excerpt,
            }
        )
    return {
        "count": len(records),
        "kinds": sorted(kinds),
        "has_text": has_text,
        "excerpts": excerpts,
    }


def _clip_title(text: str) -> str:
    if len(text) <= _MAX_TITLE_CHARS:
        return text
    clipped = text[:_MAX_TITLE_CHARS]
    head = clipped.rsplit(" ", 1)[0]
    return f"{head}…" if head else f"{clipped.rstrip()}…"


def _is_hashtag_only(text: str) -> bool:
    return not _HASHTAG_TOKEN.sub("", text).strip()


def _display_title(
    my_title: str | None,
    packet_title: str | None,
    description: str | None,
    author: str | None,
    packet: SourcePacket,
) -> str:
    if my_title:
        return my_title
    if packet_title:
        return _clip_title(packet_title)
    if description:
        for line in description.splitlines():
            stripped = line.strip()
            if stripped and not _is_hashtag_only(stripped):
                return _clip_title(stripped)
    if author:
        return f"@{author}"
    return f"{packet.source.kind.value} {packet.source.external_id or packet.source_id[:12]}"


def _snippet_for(title: str, description: str | None) -> str | None:
    if description is None or not description.strip():
        return None
    lines = description.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == title or _clip_title(stripped) == title:
            rest = "\n".join(lines[index + 1 :]).strip()
            return rest or None
        break
    return description


def _marker(collections: list[str], author: str | None, kind: str) -> str:
    if collections:
        return collections[0][:1].upper()
    if author:
        return author[:1].upper()
    return kind[:1].upper()


def _hashtags_for(description: str | None) -> list[str]:
    if not description:
        return []
    return list(find_hashtags(description))


def _annotation_view(properties: Mapping[str, object], body: str) -> dict[str, str | None]:
    def prop(key: str) -> str | None:
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    stripped = body.strip()
    if stripped.startswith(_NOTE_HEADING):
        stripped = stripped[len(_NOTE_HEADING) :].strip()
    preview: str | None = stripped if stripped else None
    if preview is not None and len(preview) > _NOTE_PREVIEW_CHARS:
        preview = preview[:_NOTE_PREVIEW_CHARS] + "…"
    return {
        "my_title": prop("my_title"),
        "why_saved": prop("why_saved"),
        "status": prop("status"),
        "note_preview": preview,
    }


def _stats_view(saved: Mapping[str, JsonValue]) -> dict[str, int]:
    value = saved.get("stats")
    if not isinstance(value, dict):
        return {}
    stats: dict[str, int] = {}
    for key in ("plays", "likes", "comments", "shares"):
        number = saved_int(value, key)
        if number is not None:
            stats[key] = number
    return stats


def _media_state(packet: SourcePacket) -> str:
    if packet.status is CaptureStatus.COMPLETE:
        return "complete"
    if any(_MEDIA_NOT_REQUESTED in limitation for limitation in packet.limitations):
        return "metadata_only"
    return "partial"


class UiViewProjection:
    NAMESPACE = "ui"

    def __init__(
        self,
        *,
        packets: SourcePacketRepository,
        artifacts: ArtifactLocator,
        store: ProjectionStore,
        extractions: ExtractionRepository | None = None,
        notes: HumanNoteReader | None = None,
        limit: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        self._packets = packets
        self._artifacts = artifacts
        self._store = store
        self._extractions = extractions
        self._notes = notes
        self._limit = limit
        self._clock = clock or (lambda: datetime.now(UTC))

    def rebuild(self) -> int:
        payload = self._build_payload()
        self._store.replace_namespace(self.NAMESPACE, [self._render_document(payload)])
        items = payload["items"]
        return len(items) if isinstance(items, list) else 0

    def render(self) -> GeneratedDocument:
        return self._render_document(self._build_payload())

    def _render_document(self, payload: dict[str, object]) -> GeneratedDocument:
        return GeneratedDocument(path="index.html", content=_render_html(payload))

    def _build_annotations(self) -> dict[str, dict[str, str | None]]:
        if self._notes is None:
            return {}
        annotations: dict[str, dict[str, str | None]] = {}
        for _, content in self._notes.iter_notes():
            parsed = parse_frontmatter(content)
            source_id = parsed.properties.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                continue
            annotations[source_id] = _annotation_view(parsed.properties, note_body(content))
        return annotations

    def _item_view(
        self,
        packet: SourcePacket,
        records: tuple[ExtractionRecord, ...],
        annotation: Mapping[str, str | None],
    ) -> dict[str, object]:
        saved = saved_index(packet)
        description = saved_str(saved, "desc")
        author = saved_str(saved, "author")
        collections = [collection.name for collection in packet.collections]
        display_title = _display_title(
            annotation["my_title"], packet.title, description, author, packet
        )
        snippet = _snippet_for(display_title, description)
        if snippet is not None and _is_hashtag_only(snippet):
            snippet = None
        return {
            "source_id": packet.source_id,
            "capture_id": packet.capture_id,
            "title": packet.title,
            "display_title": display_title,
            "snippet": snippet,
            "marker": _marker(collections, author, packet.source.kind.value),
            "hashtags": _hashtags_for(description),
            "platform": packet.source.platform,
            "kind": packet.source.kind.value,
            "external_id": packet.source.external_id,
            "canonical_url": packet.source.canonical_url,
            "captured_at": packet.captured_at.isoformat(),
            "status": packet.status.value,
            "media_state": _media_state(packet),
            "collections": collections,
            "author": author,
            "video_type": saved_str(saved, "type"),
            "post_created_at": saved_str(saved, "post_created_at"),
            "description": description,
            "stats": _stats_view(saved),
            "collector": {
                "actor_kind": packet.collector.actor_kind.value,
                "actor_id": packet.collector.actor_id,
                "method": packet.collector.method,
                "version": packet.collector.version,
            },
            "limitations": list(packet.limitations),
            "artifacts": [
                _artifact_view(artifact, self._artifacts) for artifact in packet.artifacts
            ],
            "derived": _derived_view(records),
            "annotation": dict(annotation),
        }

    def _build_payload(self) -> dict[str, object]:
        latest = latest_capture_per_source(self._packets.iter_packets())
        packets = sorted(
            latest.values(),
            key=lambda packet: (-packet.captured_at.timestamp(), packet.capture_id),
        )
        if self._limit is not None:
            packets = packets[: self._limit]
        annotations = self._build_annotations()
        items: list[dict[str, object]] = []
        status_counts: Counter[str] = Counter()
        collection_counts: Counter[str] = Counter()
        for packet in packets:
            records = (
                tuple(self._extractions.iter_for_capture(packet.capture_id))
                if self._extractions is not None
                else ()
            )
            annotation = annotations.get(packet.source_id, _EMPTY_ANNOTATION)
            items.append(self._item_view(packet, records, annotation))
            status_counts[packet.status.value] += 1
            for collection in packet.collections:
                collection_counts[collection.name] += 1
        collections = [
            {"name": name, "count": count}
            for name, count in sorted(
                collection_counts.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ]
        return {
            "schema_version": _SCHEMA_VERSION,
            "generated_at": self._clock().isoformat(),
            "generator": "sourcefolio",
            "generator_version": _GENERATOR_VERSION,
            "counts": {
                "sources": len(items),
                "complete": status_counts["complete"],
                "partial": status_counts["partial"],
            },
            "collections": collections,
            "items": items,
        }


def _render_html(payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return _HTML_TEMPLATE.replace("__SOURCEFOLIO_DATA__", data)


_HTML_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sourcefolio — архив источников</title>
<style>
:root {
  --bg: #f5f5f2;
  --surface: #ffffff;
  --surface-2: #f0f0eb;
  --border: #e6e6df;
  --border-strong: #d4d4cb;
  --text: #1a1a17;
  --text-muted: #61615a;
  --text-faint: #94948b;
  --accent: #4f46e5;
  --accent-soft: #edecfd;
  --complete: #15803d;
  --complete-soft: #e3f4e8;
  --partial: #b45309;
  --partial-soft: #fbeed6;
  --drop: #b91c1c;
  --drop-soft: #fdeaea;
  --c0: #4f46e5;
  --c1: #0d9488;
  --c2: #b45309;
  --c3: #be185d;
  --c4: #7c3aed;
  --c5: #047857;
  --c6: #0369a1;
  --c7: #a16207;
  --r-sm: 8px;
  --r: 12px;
  --shadow: 0 1px 2px rgba(20,20,18,.04), 0 4px 14px rgba(20,20,18,.05);
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  --serif: Georgia, "Times New Roman", serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #121210;
    --surface: #1b1b18;
    --surface-2: #232320;
    --border: #2d2d28;
    --border-strong: #3e3e37;
    --text: #ecece5;
    --text-muted: #a4a49a;
    --text-faint: #77776d;
    --accent: #8f96ff;
    --accent-soft: #1e2140;
    --complete: #6ee7a0;
    --complete-soft: #16301f;
    --partial: #f0b866;
    --partial-soft: #33260f;
    --drop: #f28b8b;
    --drop-soft: #3a1212;
    --c0: #8f96ff;
    --c1: #2dd4bf;
    --c2: #f0b866;
    --c3: #f472b6;
    --c4: #a78bfa;
    --c5: #34d399;
    --c6: #38bdf8;
    --c7: #facc15;
    --shadow: 0 1px 2px rgba(0,0,0,.35), 0 8px 22px rgba(0,0,0,.4);
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 900px; margin: 0 auto; padding: 0 20px; }
.topbar {
  position: sticky; top: 0; z-index: 20;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
}
.topbar-inner {
  display: flex; align-items: center; gap: 20px;
  padding-top: 12px; padding-bottom: 12px; flex-wrap: wrap;
}
.brand { display: flex; align-items: center; gap: 11px; min-width: 0; }
.brand-mark {
  width: 36px; height: 36px; border-radius: 9px; flex-shrink: 0;
  background: var(--text); color: var(--bg);
  display: grid; place-items: center;
  font-weight: 700; font-size: 13px; letter-spacing: .02em;
}
h1 {
  font-family: var(--serif); font-size: 18px; margin: 0;
  font-weight: 600; letter-spacing: -.01em; white-space: nowrap;
}
.sub { margin: 1px 0 0; color: var(--text-muted); font-size: 12px; }
.search { position: relative; flex: 1 1 240px; min-width: 180px; }
.search svg {
  position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  width: 15px; height: 15px; color: var(--text-faint); pointer-events: none;
}
.search input {
  width: 100%; padding: 9px 14px 9px 34px; font: inherit; font-size: 14px;
  border: 1px solid var(--border-strong); border-radius: 10px;
  background: var(--surface); color: var(--text);
}
.search input:focus {
  outline: 2px solid var(--accent); outline-offset: 1px;
  border-color: var(--accent);
}
main.wrap { padding-top: 14px; padding-bottom: 72px; }
.chipbar {
  display: flex; gap: 8px; overflow-x: auto; padding: 2px 0 14px;
}
.chip {
  display: inline-flex; align-items: center; gap: 7px; flex-shrink: 0;
  padding: 6px 12px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--surface);
  color: var(--text-muted); font-size: 12.5px; white-space: nowrap;
  cursor: pointer; font-family: inherit;
}
.chip:hover { border-color: var(--border-strong); color: var(--text); }
.chip[aria-pressed="true"] {
  border-color: var(--accent); background: var(--accent-soft); color: var(--text);
}
.chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.chip .n { color: var(--text-faint); font-variant-numeric: tabular-nums; }
.filters {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin-bottom: 16px;
}
.filters label {
  display: flex; align-items: center; gap: 6px;
  font-size: 12.5px; color: var(--text-muted);
}
.filters select {
  font: inherit; font-size: 13px; padding: 6px 9px;
  border: 1px solid var(--border-strong); border-radius: 8px;
  background: var(--surface); color: var(--text);
}
.result-count { margin-left: auto; font-size: 12.5px; color: var(--text-faint); }
.list { display: flex; flex-direction: column; gap: 8px; }
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); transition: border-color .15s ease, box-shadow .15s ease;
}
.card:hover { border-color: var(--border-strong); box-shadow: var(--shadow); }
.card:focus-within { border-color: var(--border-strong); }
.card.open { border-color: var(--border-strong); box-shadow: var(--shadow); }
.card-row {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 12px 12px 12px 14px;
}
.card-row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.avatar {
  width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0;
  display: grid; place-items: center;
  font-weight: 700; font-size: 16px; line-height: 1;
  background: var(--accent-soft);
  color: var(--accent);
}
.card-main { flex: 1 1 auto; min-width: 0; }
.titleline { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.title {
  font-size: 15px; font-weight: 600; margin: 0;
  letter-spacing: -.005em; overflow-wrap: anywhere;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.pill {
  font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 999px;
  white-space: nowrap; letter-spacing: .01em; flex-shrink: 0;
}
.pill-kind { background: var(--surface-2); color: var(--text-faint); }
.pill-partial { background: var(--partial-soft); color: var(--partial); }
.badge-inbox { background: var(--surface-2); color: var(--text-muted); }
.badge-keep { background: var(--complete-soft); color: var(--complete); }
.badge-done { background: var(--accent-soft); color: var(--accent); }
.badge-drop { background: var(--drop-soft); color: var(--drop); }
.meta {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-top: 3px; font-size: 12.5px; color: var(--text-muted);
}
.meta-item { display: inline-flex; align-items: center; gap: 4px; }
.meta-item svg { width: 13px; height: 13px; color: var(--text-faint); flex-shrink: 0; }
.meta-item.author { font-weight: 500; color: var(--text-muted); }
.stat { font-variant-numeric: tabular-nums; }
.tags { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 6px; }
.tag {
  font-size: 11px; padding: 1px 7px; border-radius: 6px;
  background: var(--surface-2); color: var(--text-muted);
}
.snippet {
  margin: 6px 0 0; font-size: 13px; color: var(--text-muted);
  white-space: pre-line;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.why {
  margin: 6px 0 0; font-size: 12.5px; padding: 5px 10px; border-radius: var(--r-sm);
  background: var(--accent-soft); color: var(--accent); overflow-wrap: anywhere;
}
.note-line { margin: 5px 0 0; font-size: 12.5px; color: var(--text-muted); }
.card-side { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
.icon-btn {
  width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center;
  color: var(--text-faint); text-decoration: none; cursor: pointer;
}
.icon-btn:hover { background: var(--surface-2); color: var(--accent); }
.icon-btn svg { width: 16px; height: 16px; }
.chevron {
  width: 26px; height: 30px; display: grid; place-items: center;
  color: var(--text-faint); cursor: pointer;
}
.chevron svg { width: 16px; height: 16px; transition: transform .18s ease; }
.card.open .chevron svg { transform: rotate(180deg); }
.details { display: none; padding: 0 14px 14px 64px; }
.card.open .details { display: block; }
.block { margin-bottom: 14px; }
.block:last-child { margin-bottom: 0; }
.block h3 {
  font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--text-faint); margin: 0 0 6px; font-weight: 700;
}
.kv {
  display: grid; grid-template-columns: minmax(0, 120px) minmax(0, 1fr);
  gap: 3px 12px; margin: 0; font-size: 13px;
}
.kv dt { color: var(--text-faint); }
.kv dd { margin: 0; font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.kv dd a { color: var(--accent); }
.desc { margin: 0; font-size: 13.5px; white-space: pre-wrap; overflow-wrap: anywhere; }
.hint { margin: 0; font-size: 12.5px; color: var(--text-faint); }
.limitations { margin: 0; padding-left: 18px; font-size: 13px; color: var(--text-muted); }
.artifacts {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px;
}
.artifact {
  border: 1px solid var(--border); border-radius: var(--r-sm);
  overflow: hidden; background: var(--surface-2);
}
.art-media { display: block; width: 100%; max-height: 200px; object-fit: cover; background: #000; }
.art-meta { padding: 7px 9px; display: flex; flex-direction: column; gap: 2px; }
.art-role { font-size: 12px; font-weight: 600; }
.art-sub { font-size: 11px; color: var(--text-faint); font-family: var(--mono); }
.art-link { font-size: 12px; color: var(--accent); margin-top: 2px; }
.excerpt {
  border: 1px solid var(--border); border-radius: var(--r-sm);
  padding: 9px 11px; margin-bottom: 7px; background: var(--surface-2);
}
.excerpt-head {
  display: flex; gap: 10px; font-size: 11px; color: var(--text-faint);
  font-family: var(--mono); margin-bottom: 5px; flex-wrap: wrap;
}
.excerpt-kind { color: var(--accent); font-weight: 600; }
.excerpt-text { margin: 0; font-size: 13px; white-space: pre-wrap; }
.empty { color: var(--text-faint); text-align: center; padding: 48px 0; }
.show-more {
  display: block; margin: 16px auto 0; padding: 9px 18px;
  font: inherit; font-size: 13.5px;
  border: 1px solid var(--border-strong); border-radius: 10px;
  background: var(--surface); color: var(--text); cursor: pointer;
}
.show-more:hover { border-color: var(--accent); }
@media (max-width: 560px) {
  .kv { grid-template-columns: 1fr; gap: 0; }
  .kv dt { margin-top: 7px; }
  .details { padding-left: 14px; }
  h1 { font-size: 16px; }
}
</style>
</head>
<body>
<header class="topbar">
  <div class="wrap topbar-inner">
    <div class="brand">
      <span class="brand-mark">SF</span>
      <div>
        <h1>Архив источников</h1>
        <p class="sub" id="summary"></p>
      </div>
    </div>
    <div class="search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="11" cy="11" r="8"></circle>
        <path d="M21 21l-4.3-4.3"></path>
      </svg>
      <input id="q" type="search" autocomplete="off"
        placeholder="Поиск по названию, автору, коллекции, подписи…">
    </div>
  </div>
</header>
<main class="wrap">
  <nav class="chipbar" id="chipbar" aria-label="Коллекции"></nav>
  <div class="filters">
    <label>Коллекция
      <select id="f-collection"><option value="">Любые</option></select>
    </label>
    <label>Тип
      <select id="f-kind"><option value="">Любые</option></select>
    </label>
    <label>Статус
      <select id="f-status">
        <option value="">Любые</option>
        <option value="complete">полные</option>
        <option value="partial">частичные</option>
      </select>
    </label>
    <label>Мой статус
      <select id="f-note-status">
        <option value="">Любые</option>
        <option value="inbox">inbox</option>
        <option value="keep">keep</option>
        <option value="drop">drop</option>
        <option value="done">done</option>
      </select>
    </label>
    <span class="result-count" id="result-count"></span>
  </div>
  <div id="list" class="list"></div>
  <button id="show-more" class="show-more" hidden></button>
</main>
<script id="sourcefolio-data" type="application/json">__SOURCEFOLIO_DATA__</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById('sourcefolio-data').textContent);
  var NS = 'http://www.w3.org/2000/svg';
  var CHUNK = 100;
  var STEP = 200;
  var shown = CHUNK;
  var list = document.getElementById('list');
  var q = document.getElementById('q');
  var chipbar = document.getElementById('chipbar');
  var fCollection = document.getElementById('f-collection');
  var fKind = document.getElementById('f-kind');
  var fStatus = document.getElementById('f-status');
  var fNoteStatus = document.getElementById('f-note-status');
  var resultCount = document.getElementById('result-count');
  var showMore = document.getElementById('show-more');
  var summary = document.getElementById('summary');

  var PATHS = {
    user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8z',
    calendar: 'M8 2v4M16 2v4M3 10h18' +
      'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
    play: 'M7 4v16l12-8z',
    heart: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1' +
      'a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z',
    external: 'M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6',
    share: 'M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13',
    chevron: 'M6 9l6 6 6-6'
  };

  function icon(name) {
    var s = document.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none');
    s.setAttribute('stroke', 'currentColor');
    s.setAttribute('stroke-width', '2');
    s.setAttribute('stroke-linecap', 'round');
    s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(NS, 'path');
    p.setAttribute('d', PATHS[name]);
    s.appendChild(p);
    return s;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text != null) { n.textContent = text; }
    return n;
  }

  function pill(text, cls) { return el('span', 'pill ' + cls, text); }

  function fmtDate(iso) {
    if (!iso) { return ''; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return iso; }
    return d.toISOString().slice(0, 10);
  }

  function fmtSize(bytes) {
    if (bytes == null) { return ''; }
    if (bytes < 1024) { return bytes + ' B'; }
    if (bytes < 1048576) { return (bytes / 1024).toFixed(1) + ' KB'; }
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function fmtInt(n) {
    if (n == null) { return ''; }
    if (n >= 1000000) { return (n / 1000000).toFixed(1) + 'M'; }
    if (n >= 1000) { return (n / 1000).toFixed(1) + 'K'; }
    return String(n);
  }

  function accentKey(item) {
    return (item.collections && item.collections[0]) || item.author || item.kind || '';
  }

  function accentVar(key) {
    var h = 0;
    for (var i = 0; i < key.length; i++) { h = (h * 31 + key.charCodeAt(i)) >>> 0; }
    return 'var(--c' + (h % 8) + ')';
  }

  function metaItem(name, text, cls) {
    var n = el('span', 'meta-item' + (cls ? ' ' + cls : ''));
    n.appendChild(icon(name));
    n.appendChild(el('span', 'stat', text));
    return n;
  }

  function addKV(dl, key, value) {
    dl.appendChild(el('dt', null, key));
    dl.appendChild(el('dd', null, value));
  }

  function addLink(dl, key, url) {
    dl.appendChild(el('dt', null, key));
    var dd = el('dd');
    var a = el('a', null, url);
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    dd.appendChild(a);
    dl.appendChild(dd);
  }

  function buildCard(item) {
    var ann = item.annotation || {};
    var card = el('article', 'card');
    card.style.setProperty('--accent', accentVar(accentKey(item)));

    var row = el('div', 'card-row');
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.setAttribute('aria-expanded', 'false');
    row.appendChild(el('span', 'avatar', item.marker));

    var main = el('div', 'card-main');
    var titleline = el('div', 'titleline');
    titleline.appendChild(el('h2', 'title', item.display_title));
    titleline.appendChild(pill(item.kind, 'pill-kind'));
    if (item.media_state === 'partial') { titleline.appendChild(pill('частично', 'pill-partial')); }
    if (ann.status && ann.status !== 'inbox') {
      titleline.appendChild(pill(ann.status, 'badge-' + ann.status));
    }
    main.appendChild(titleline);

    var meta = el('div', 'meta');
    if (item.author && item.display_title !== '@' + item.author) {
      meta.appendChild(metaItem('user', '@' + item.author, 'author'));
    }
    if (item.post_created_at) {
      meta.appendChild(metaItem('calendar', fmtDate(item.post_created_at)));
    }
    if (item.stats.plays != null) { meta.appendChild(metaItem('play', fmtInt(item.stats.plays))); }
    if (item.stats.likes != null) { meta.appendChild(metaItem('heart', fmtInt(item.stats.likes))); }
    if (meta.childNodes.length) { main.appendChild(meta); }

    if (item.hashtags.length) {
      var tags = el('div', 'tags');
      item.hashtags.slice(0, 8).forEach(function (t) {
        tags.appendChild(el('span', 'tag', '#' + t));
      });
      main.appendChild(tags);
    }
    if (ann.my_title && ann.my_title !== item.display_title) {
      main.appendChild(el('p', 'note-line', 'моё название: ' + ann.my_title));
    }
    if (ann.why_saved) { main.appendChild(el('p', 'why', 'Зачем: ' + ann.why_saved)); }
    if (item.snippet) { main.appendChild(el('p', 'snippet', item.snippet)); }

    row.appendChild(main);

    var side = el('div', 'card-side');
    if (item.canonical_url) {
      var open = el('a', 'icon-btn');
      open.href = item.canonical_url;
      open.target = '_blank';
      open.rel = 'noopener';
      open.title = 'открыть в TikTok';
      open.appendChild(icon('external'));
      side.appendChild(open);
      var share = el('a', 'icon-btn');
      share.href = 'https://t.me/share/url?url=' +
        encodeURIComponent(item.canonical_url) + '&text=' +
        encodeURIComponent(item.display_title);
      share.target = '_blank';
      share.rel = 'noopener';
      share.title = 'поделиться';
      share.appendChild(icon('share'));
      side.appendChild(share);
    }
    var chev = el('span', 'chevron');
    chev.appendChild(icon('chevron'));
    side.appendChild(chev);
    row.appendChild(side);

    card.appendChild(row);

    var details = el('div', 'details');

    var prov = el('section', 'block');
    prov.appendChild(el('h3', null, 'Провенанс'));
    var dl = el('dl', 'kv');
    addKV(dl, 'source_id', item.source_id);
    addKV(dl, 'capture_id', item.capture_id);
    if (item.title) { addKV(dl, 'title', item.title); }
    if (item.external_id) { addKV(dl, 'external_id', item.external_id); }
    if (item.canonical_url) { addLink(dl, 'canonical_url', item.canonical_url); }
    if (item.collections.length) { addKV(dl, 'collections', item.collections.join(', ')); }
    var collector = item.collector.actor_kind + ':' + item.collector.actor_id +
      ' · ' + item.collector.method;
    if (item.collector.version) { collector += ' · v' + item.collector.version; }
    addKV(dl, 'collector', collector);
    addKV(dl, 'захвачено', fmtDate(item.captured_at));
    if (item.video_type) { addKV(dl, 'type', item.video_type); }
    if (item.post_created_at) { addKV(dl, 'posted', item.post_created_at); }
    prov.appendChild(dl);
    details.appendChild(prov);

    if (ann.my_title || ann.why_saved || ann.status || ann.note_preview) {
      var noteBlock = el('section', 'block');
      noteBlock.appendChild(el('h3', null, 'Моя подпись'));
      var nd = el('dl', 'kv');
      if (ann.my_title) { addKV(nd, 'моё название', ann.my_title); }
      if (ann.why_saved) { addKV(nd, 'зачем', ann.why_saved); }
      if (ann.status) { addKV(nd, 'статус', ann.status); }
      if (ann.note_preview) { addKV(nd, 'заметка', ann.note_preview); }
      noteBlock.appendChild(nd);
      details.appendChild(noteBlock);
    }

    if (item.description) {
      var desc = el('section', 'block');
      desc.appendChild(el('h3', null, 'Описание'));
      desc.appendChild(el('p', 'desc', item.description));
      details.appendChild(desc);
    }

    if (item.media_state === 'metadata_only') {
      var med = el('section', 'block');
      med.appendChild(el('h3', null, 'Медиа'));
      med.appendChild(el('p', 'hint', 'сохранены только метаданные — медиа не скачивалось'));
      details.appendChild(med);
    } else if (item.limitations.length) {
      var lim = el('section', 'block');
      lim.appendChild(el('h3', null, 'Ограничения'));
      var ul = el('ul', 'limitations');
      item.limitations.forEach(function (t) { ul.appendChild(el('li', null, t)); });
      lim.appendChild(ul);
      details.appendChild(lim);
    }

    var art = el('section', 'block');
    art.appendChild(el('h3', null, 'Артефакты (' + item.artifacts.length + ')'));
    var grid = el('div', 'artifacts');
    item.artifacts.forEach(function (a) {
      var box = el('div', 'artifact');
      if (a.is_image) {
        var img = el('img', 'art-media');
        img.src = a.uri;
        img.alt = a.role;
        img.loading = 'lazy';
        box.appendChild(img);
      } else if (a.is_video) {
        var v = el('video', 'art-media');
        v.src = a.uri;
        v.controls = true;
        v.preload = 'metadata';
        box.appendChild(v);
      }
      var metaBox = el('div', 'art-meta');
      metaBox.appendChild(el('span', 'art-role', a.role));
      metaBox.appendChild(el('span', 'art-sub', a.media_type + ' · ' + fmtSize(a.size_bytes)));
      var link = el('a', 'art-link', 'открыть');
      link.href = a.uri;
      link.target = '_blank';
      link.rel = 'noopener';
      metaBox.appendChild(link);
      box.appendChild(metaBox);
      grid.appendChild(box);
    });
    art.appendChild(grid);
    details.appendChild(art);

    if (item.derived.count) {
      var der = el('section', 'block');
      der.appendChild(el('h3', null, 'Извлечённый текст (' + item.derived.count + ')'));
      item.derived.excerpts.forEach(function (ex) {
        var exBox = el('div', 'excerpt');
        var exHead = el('div', 'excerpt-head');
        exHead.appendChild(el('span', 'excerpt-kind', ex.kind));
        exHead.appendChild(el('span', 'excerpt-loc', ex.locator));
        exHead.appendChild(el('span', 'excerpt-ext', ex.extractor));
        exBox.appendChild(exHead);
        exBox.appendChild(el('p', 'excerpt-text', ex.text));
        der.appendChild(exBox);
      });
      details.appendChild(der);
    }

    card.appendChild(details);

    function toggle() {
      var open = card.classList.toggle('open');
      row.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    row.addEventListener('click', function (e) {
      if (e.target.closest('a, video, img')) { return; }
      toggle();
    });
    row.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggle();
      }
    });
    return card;
  }

  function matches(item, term, collection, kind, status, noteStatus) {
    if (collection && item.collections.indexOf(collection) === -1) { return false; }
    if (kind && item.kind !== kind) { return false; }
    if (status && item.status !== status) { return false; }
    var ann = item.annotation || {};
    if (noteStatus && ann.status !== noteStatus) { return false; }
    if (!term) { return true; }
    var hay = [item.display_title, item.title, item.snippet, item.description, item.author,
      item.external_id, item.collections.join(' '), item.hashtags.join(' '),
      ann.my_title, ann.why_saved, ann.note_preview]
      .filter(Boolean).join(' ').toLowerCase();
    return hay.indexOf(term) !== -1;
  }

  var annotated = 0;
  data.items.forEach(function (it) {
    var a = it.annotation || {};
    if (a.my_title || a.why_saved || a.note_preview) { annotated++; }
  });

  var parts = [data.counts.sources + ' источников', data.collections.length + ' коллекций'];
  if (annotated) { parts.push(annotated + ' подписано'); }
  summary.textContent = parts.join(' · ');

  var chipEls = [];
  function setCollection(name) {
    fCollection.value = name;
    chipEls.forEach(function (c) {
      c.node.setAttribute('aria-pressed', c.name === name ? 'true' : 'false');
    });
    render(true);
  }

  function buildChips() {
    var all = document.createElement('button');
    all.type = 'button';
    all.className = 'chip';
    all.setAttribute('aria-pressed', 'true');
    all.appendChild(el('span', null, 'весь архив'));
    all.appendChild(el('span', 'n', String(data.counts.sources)));
    all.addEventListener('click', function () { setCollection(''); });
    chipbar.appendChild(all);
    chipEls.push({ name: '', node: all });

    data.collections.forEach(function (c) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.setAttribute('aria-pressed', 'false');
      var dot = el('span', 'dot');
      dot.style.background = accentVar(c.name);
      b.appendChild(dot);
      b.appendChild(el('span', null, c.name));
      b.appendChild(el('span', 'n', String(c.count)));
      b.addEventListener('click', function () { setCollection(c.name); });
      chipbar.appendChild(b);
      chipEls.push({ name: c.name, node: b });
    });
  }

  data.collections.forEach(function (c) {
    var o = document.createElement('option');
    o.value = c.name;
    o.textContent = c.name + ' (' + c.count + ')';
    fCollection.appendChild(o);
  });
  var kinds = {};
  data.items.forEach(function (it) { kinds[it.kind] = true; });
  Object.keys(kinds).sort().forEach(function (k) {
    var o = document.createElement('option');
    o.value = k;
    o.textContent = k;
    fKind.appendChild(o);
  });

  function render(reset) {
    if (reset) { shown = CHUNK; }
    var term = q.value.trim().toLowerCase();
    var filtered = data.items.filter(function (it) {
      return matches(it, term, fCollection.value, fKind.value, fStatus.value,
        fNoteStatus.value);
    });
    list.textContent = '';
    var slice = filtered.slice(0, shown);
    slice.forEach(function (it) { list.appendChild(buildCard(it)); });
    if (!filtered.length) {
      list.appendChild(el('p', 'empty', 'Ничего не найдено.'));
    }
    resultCount.textContent = 'Показано ' + slice.length + ' из ' + filtered.length;
    if (filtered.length > shown) {
      showMore.hidden = false;
      showMore.textContent = 'Показать ещё (' + (filtered.length - shown) + ')';
    } else {
      showMore.hidden = true;
    }
  }

  buildChips();
  q.addEventListener('input', function () { render(true); });
  fCollection.addEventListener('change', function () {
    chipEls.forEach(function (c) {
      c.node.setAttribute('aria-pressed', c.name === fCollection.value ? 'true' : 'false');
    });
    render(true);
  });
  fKind.addEventListener('change', function () { render(true); });
  fStatus.addEventListener('change', function () { render(true); });
  fNoteStatus.addEventListener('change', function () { render(true); });
  showMore.addEventListener('click', function () { shown += STEP; render(false); });
  render(true);
})();
</script>
</body>
</html>
"""
