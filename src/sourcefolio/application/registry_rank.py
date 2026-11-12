from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from sourcefolio.application.entity_extraction import find_repositories
from sourcefolio.application.extraction_ports import ExtractionRepository
from sourcefolio.application.latest_captures import latest_capture_per_source
from sourcefolio.application.saved_index import (
    saved_comments,
    saved_index,
    saved_int,
    saved_str,
)
from sourcefolio.domain.source import CaptureId, SourcePacket

_CODE_MARKERS = (
    "```",
    "def ",
    "import ",
    "pip install",
    "npm i",
    "docker",
    "curl ",
    "mcp",
    "prompt",
)


@dataclass(frozen=True)
class CaptureScore:
    source_id: str
    capture_id: str
    score: int
    has_repository: bool
    has_code: bool
    has_text: bool
    comment_count: int
    engagement_ratio: float
    description_key: str


def _normalize_description(description: str) -> str:
    return " ".join(description.casefold().split())


def _description_key(description: str) -> str:
    normalized = _normalize_description(description)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _ocr_text(
    extractions: ExtractionRepository | None,
    capture_id: CaptureId,
    max_ocr_kb: int,
) -> str:
    if extractions is None:
        return ""
    parts: list[str] = []
    for record in extractions.iter_for_capture(capture_id):
        text = record.text.strip()
        if text:
            parts.append(text)
    return _truncate_utf8("\n".join(parts), max_ocr_kb * 1024)


def score_capture(packet: SourcePacket, ocr_text: str) -> CaptureScore:
    saved = saved_index(packet)
    description = saved_str(saved, "desc") or ""
    comments = saved_comments(saved)
    comment_texts = tuple(
        text
        for comment in saved_comments(saved)
        if (text := saved_str(comment, "text")) is not None
    )
    stats_value = saved.get("stats")
    stats = stats_value if isinstance(stats_value, dict) else {}
    plays = saved_int(stats, "plays") or 0
    likes = saved_int(stats, "likes") or 0
    engagement_ratio = likes / plays if plays > 0 else 0.0

    has_repository = bool(find_repositories(description)) or any(
        find_repositories(text) for text in comment_texts
    )
    folded_ocr = ocr_text.casefold()
    has_code = any(marker in folded_ocr for marker in _CODE_MARKERS)
    has_text = bool(description.strip()) or bool(comment_texts) or bool(ocr_text.strip())

    score = 0
    if has_repository:
        score += 3
    if has_code:
        score += 2
    if len(description.strip()) >= 40:
        score += 2
    if len(comments) >= 3:
        score += 1
    if plays > 0 and engagement_ratio >= 0.02:
        score += 1
    if not has_text:
        score -= 3

    return CaptureScore(
        source_id=packet.source_id,
        capture_id=packet.capture_id,
        score=score,
        has_repository=has_repository,
        has_code=has_code,
        has_text=has_text,
        comment_count=len(comments),
        engagement_ratio=engagement_ratio,
        description_key=_description_key(description),
    )


def _with_score(item: CaptureScore, score: int) -> CaptureScore:
    return CaptureScore(
        source_id=item.source_id,
        capture_id=item.capture_id,
        score=score,
        has_repository=item.has_repository,
        has_code=item.has_code,
        has_text=item.has_text,
        comment_count=item.comment_count,
        engagement_ratio=item.engagement_ratio,
        description_key=item.description_key,
    )


def rank_captures(
    packets: Iterable[SourcePacket],
    extractions: ExtractionRepository | None,
    *,
    max_ocr_kb: int = 20,
) -> tuple[CaptureScore, ...]:
    latest = latest_capture_per_source(packets)
    base: dict[str, CaptureScore] = {}
    for packet in latest.values():
        ocr_text = _ocr_text(extractions, packet.capture_id, max_ocr_kb)
        base[packet.capture_id] = score_capture(packet, ocr_text)

    ranked = sorted(
        latest.values(),
        key=lambda packet: (
            -base[packet.capture_id].score,
            -packet.captured_at.timestamp(),
            packet.capture_id,
        ),
    )
    seen: set[str] = set()
    penalties: dict[str, int] = {}
    for packet in ranked:
        key = base[packet.capture_id].description_key
        if not key:
            continue
        if key in seen:
            penalties[packet.capture_id] = -1
        else:
            seen.add(key)

    final = sorted(
        ranked,
        key=lambda packet: (
            -(base[packet.capture_id].score + penalties.get(packet.capture_id, 0)),
            -packet.captured_at.timestamp(),
            packet.capture_id,
        ),
    )
    return tuple(
        _with_score(
            base[packet.capture_id],
            base[packet.capture_id].score + penalties.get(packet.capture_id, 0),
        )
        for packet in final
    )
