from __future__ import annotations

import json
import os
import re
import sys
import threading
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from sourcefolio.application.note import NoteWriter
from sourcefolio.application.ports import ArtifactStore
from sourcefolio.application.ui_view import UiViewProjection, display_title_for
from sourcefolio.domain.source import SourcePacket

_ARTIFACT_PATH = re.compile(r"^/artifacts/(art_[0-9a-f]{64})$")
_ARTIFACT_ID = re.compile(r"^art_[0-9a-f]{64}$")
_MAX_BODY_BYTES = 64 * 1024
_CHUNK_BYTES = 64 * 1024
_STATUSES = ("inbox", "keep", "drop", "done")
_CLEARABLE = ("my_title", "why_saved")
_CONTENT_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
    "video/mp4": "video/mp4",
    "video/webm": "video/webm",
}


class _Context:
    def __init__(
        self,
        *,
        projection: UiViewProjection,
        artifacts: ArtifactStore,
        notes: NoteWriter,
        packets: Mapping[str, SourcePacket],
    ) -> None:
        self.projection = projection
        self.artifacts = artifacts
        self.notes = notes
        self.packets = dict(packets)
        self.media_types = {
            artifact.artifact_id: artifact.media_type
            for packet in packets.values()
            for artifact in packet.artifacts
        }
        self.render_lock = threading.Lock()
        self.note_lock = threading.Lock()


def _parse_range(header: str | None, size: int) -> tuple[int, int, bool] | None:
    if not header:
        return (0, size - 1, False)
    if not header.startswith("bytes=") or "," in header:
        return None
    start_text, _, end_text = header[len("bytes=") :].strip().partition("-")
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        else:
            length = int(end_text)
            start = max(0, size - length)
            end = size - 1
    except ValueError:
        return None
    if start >= size or start > end:
        return None
    return (start, min(end, size - 1), True)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("ui-serve %s\n" % (format % args))
        sys.stderr.flush()

    @property
    def _context(self) -> _Context:
        return cast(_UiHttpServer, self.server).context

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with self._context.render_lock:
                document = self._context.projection.render()
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", document.content.encode())
            return
        match = _ARTIFACT_PATH.fullmatch(path)
        if match is not None:
            self._send_artifact(match.group(1))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/note":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if self.headers.get("X-Sourcefolio") != "1":
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "missing X-Sourcefolio header"})
            return
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        if content_type != "application/json":
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected json"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "empty body"})
            return
        if length > _MAX_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body is too large"})
            return
        try:
            parsed: object = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return
        if not isinstance(parsed, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return
        self._update_note({str(key): value for key, value in parsed.items()})

    def _update_note(self, payload: dict[str, object]) -> None:
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "source_id is required"})
            return
        status = payload.get("status")
        if status is not None and status not in _STATUSES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid status"})
            return
        texts: dict[str, str] = {}
        for key in ("my_title", "why_saved"):
            value = payload.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"invalid {key}"})
                return
            texts[key] = value
        raw_clear = payload.get("clear") or []
        if not isinstance(raw_clear, list) or any(
            not isinstance(entry, str) or entry not in _CLEARABLE for entry in raw_clear
        ):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid clear"})
            return
        packet = self._context.packets.get(source_id)
        if packet is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown source_id"})
            return
        if status is None and not texts and not raw_clear:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "nothing to set"})
            return
        try:
            with self._context.note_lock:
                result = self._context.notes.set_packet(
                    packet,
                    my_title=texts.get("my_title"),
                    why_saved=texts.get("why_saved"),
                    status=status,
                    clear=tuple(cast(list[str], raw_clear)),
                )
        except ValueError as error:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
            return
        response: dict[str, object] = {
            "note": result.note,
            "created": result.created,
            "applied": result.applied,
        }
        if "my_title" in texts or "my_title" in raw_clear:
            response["display_title"] = display_title_for(packet, texts.get("my_title"))
        self._send_json(HTTPStatus.OK, response)

    def _send_artifact(self, artifact_id: str) -> None:
        if _ARTIFACT_ID.fullmatch(artifact_id) is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown artifact"})
            return
        media_type = self._context.media_types.get(artifact_id)
        if media_type is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown artifact"})
            return
        try:
            stream = self._context.artifacts.open(artifact_id)
        except (OSError, ValueError):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown artifact"})
            return
        with stream:
            size = os.fstat(stream.fileno()).st_size
            span = _parse_range(self.headers.get("Range"), size)
            if span is None:
                self._send_json(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    {"error": "invalid range"},
                    {"Content-Range": f"bytes */{size}"},
                )
                return
            start, end, partial = span
            self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
            self.send_header("Content-Type", _CONTENT_TYPES.get(media_type, media_type))
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            stream.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = stream.read(min(_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _send_json(
        self,
        status: HTTPStatus,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body, headers)

    def _send(
        self,
        status: HTTPStatus,
        content_type: str,
        body: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class _UiHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], context: _Context) -> None:
        self.context = context
        super().__init__(address, _Handler)


class UiHttpServer:
    def __init__(
        self,
        *,
        projection: UiViewProjection,
        artifacts: ArtifactStore,
        notes: NoteWriter,
        packets: Mapping[str, SourcePacket],
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self._context = _Context(
            projection=projection,
            artifacts=artifacts,
            notes=notes,
            packets=packets,
        )
        self._server = _UiHttpServer((host, port), self._context)

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return (str(host), int(port))

    @property
    def sources(self) -> int:
        return len(self._context.packets)

    @property
    def url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}/"

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
