from __future__ import annotations

import argparse
import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from sourcefolio.application.ui_serve import UiHttpServer
from sourcefolio.cli import _build_serve_server, main

MANIFEST = Path(__file__).parents[2] / "examples" / "manual-capture" / "manifest.json"
_DATA_SCRIPT = re.compile(
    rb'<script id="sourcefolio-data" type="application/json">(.*?)</script>', re.S
)


@pytest.fixture()
def served(tmp_path: Path, capsys) -> Iterator[tuple[str, Path]]:
    archive = tmp_path / "archive"
    vault = tmp_path / "vault"
    assert main(["archive-capture", str(MANIFEST), "--archive-dir", str(archive)]) == 0
    capsys.readouterr()
    args = argparse.Namespace(
        archive_dir=str(archive),
        derived_dir=str(tmp_path / "derived"),
        vault=str(vault),
        host="127.0.0.1",
        port=0,
    )
    server = _build_serve_server(args)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.url, vault
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _request(
    url: str, data: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _post(
    base: str, payload: object, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, object]]:
    merged = {"Content-Type": "application/json", **(headers or {})}
    status, _, body = _request(base + "api/note", json.dumps(payload).encode(), merged)
    parsed: dict[str, object] = json.loads(body)
    return status, parsed


def _payload(page: bytes) -> dict[str, object]:
    match = _DATA_SCRIPT.search(page)
    assert match is not None
    parsed: dict[str, object] = json.loads(match.group(1))
    return parsed


def _only_item(page: bytes) -> dict[str, object]:
    items = _payload(page)["items"]
    assert isinstance(items, list) and len(items) == 1
    item = items[0]
    assert isinstance(item, dict)
    return item


def test_serves_page_with_embedded_payload(served: tuple[str, Path]) -> None:
    url, _ = served
    status, headers, body = _request(url)
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    item = _only_item(body)
    assert item["display_title"] == "Synthetic saved video"
    annotation = item["annotation"]
    assert isinstance(annotation, dict)
    assert annotation["status"] is None


def test_serves_artifact_with_range_support(served: tuple[str, Path]) -> None:
    url, _ = served
    item = _only_item(_request(url)[2])
    artifacts = item["artifacts"]
    assert isinstance(artifacts, list)
    artifact = artifacts[0]
    uri = artifact["uri"]
    assert isinstance(uri, str)

    status, headers, full_body = _request(url.rstrip("/") + uri)
    size = len(full_body)
    assert status == 200
    assert headers["Content-Type"].startswith("text/plain")
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Length"] == str(size)
    assert full_body

    status, headers, body = _request(url.rstrip("/") + uri, headers={"Range": "bytes=0-6"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes 0-6/{size}"
    assert body == full_body[:7]

    assert _request(url.rstrip("/") + uri, headers={"Range": "bytes=999999-"})[0] == 416


def test_unknown_paths_return_json_404(served: tuple[str, Path]) -> None:
    url, _ = served
    status, headers, body = _request(url + "missing")
    assert status == 404
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body) == {"error": "not found"}
    assert _request(url + "artifacts/art_" + "0" * 64)[0] == 404


def test_note_api_updates_human_note_and_annotation(served: tuple[str, Path]) -> None:
    url, vault = served
    source_id = _only_item(_request(url + "index.html")[2])["source_id"]
    assert isinstance(source_id, str)
    note = vault / "ideas" / f"{source_id}.md"

    status, body = _post(url, {"source_id": source_id, "status": "keep"}, {"X-Sourcefolio": "1"})
    assert status == 200
    assert body["applied"] == {"status": "keep"}
    assert "display_title" not in body
    assert "status: keep" in note.read_text(encoding="utf-8")

    status, body = _post(
        url, {"source_id": source_id, "my_title": "Мой заголовок"}, {"X-Sourcefolio": "1"}
    )
    assert status == 200
    assert body["display_title"] == "Мой заголовок"
    assert _only_item(_request(url)[2])["display_title"] == "Мой заголовок"

    status, body = _post(
        url, {"source_id": source_id, "clear": ["my_title"]}, {"X-Sourcefolio": "1"}
    )
    assert status == 200
    assert body["applied"] == {"my_title": None}
    assert body["display_title"] == "Synthetic saved video"
    assert "my_title:" not in note.read_text(encoding="utf-8")


def test_note_api_rejects_bad_requests(served: tuple[str, Path]) -> None:
    url, _ = served
    source_id = _only_item(_request(url)[2])["source_id"]
    guarded = {"X-Sourcefolio": "1"}

    assert _post(url, {"source_id": source_id, "status": "keep"})[0] == 403
    assert _post(url, {"status": "keep"}, guarded)[0] == 400
    assert _post(url, {"source_id": source_id, "status": "nope"}, guarded)[0] == 400
    assert _post(url, {"source_id": "src_" + "0" * 64, "status": "keep"}, guarded)[0] == 404
    assert _post(url, {"source_id": source_id}, guarded)[0] == 400
    assert _post(url, {"source_id": source_id, "clear": ["status"]}, guarded)[0] == 400
    assert _post(url, {"source_id": source_id, "my_title": 7}, guarded)[0] == 400


def test_note_api_rejects_non_json(served: tuple[str, Path]) -> None:
    url, _ = served
    headers = {"X-Sourcefolio": "1", "Content-Type": "text/plain"}
    assert _request(url + "api/note", b"{}", headers)[0] == 415

    headers["Content-Type"] = "application/json"
    assert _request(url + "api/note", b"{", headers)[0] == 400


def test_serve_command_reports_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    calls: list[UiHttpServer] = []

    monkeypatch.setattr(UiHttpServer, "serve_forever", lambda self: calls.append(self))
    monkeypatch.setattr(UiHttpServer, "shutdown", lambda self: None)
    assert (
        main(
            [
                "serve",
                "--archive-dir",
                str(tmp_path / "archive"),
                "--derived-dir",
                str(tmp_path / "derived"),
                "--vault",
                str(tmp_path / "vault"),
                "--port",
                "0",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["url"].startswith("http://127.0.0.1:")
    assert output["sources"] == 0
    assert len(calls) == 1
