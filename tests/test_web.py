import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from morning_digest.config import Preset
from morning_digest.storage import Storage
from morning_digest.web import make_handler


def _request(server, path, *, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        response = urlopen(request, timeout=2)
    except HTTPError as error:
        response = error
    return response.status, json.loads(response.read())


def test_signup_api_lists_presets_and_creates_subscriber(tmp_path: Path):
    from http.server import ThreadingHTTPServer

    settings = SimpleNamespace(
        database_path=tmp_path / "db.sqlite",
        presets=(Preset("brief", "Morning Brief", "Only the essentials.", frozenset({"news"})),),
    )
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><title>Test</title>", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(settings, index))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, catalog = _request(server, "/api/presets")
        assert status == 200
        assert catalog["presets"][0]["id"] == "brief"

        status, result = _request(
            server,
            "/api/subscribers",
            payload={"email": "reader@example.com", "preset_id": "brief"},
        )
        assert status == 201
        assert result["status"] == "subscribed"
        storage = Storage(settings.database_path)
        assert storage.active_subscribers()[0][1:3] == ("reader@example.com", "brief")
        storage.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_signup_api_rejects_unknown_preset(tmp_path: Path):
    from http.server import ThreadingHTTPServer

    settings = SimpleNamespace(
        database_path=tmp_path / "db.sqlite",
        presets=(Preset("brief", "Brief", "Essentials.", frozenset({"news"})),),
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(settings, tmp_path / "index.html")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, result = _request(
            server,
            "/api/subscribers",
            payload={"email": "reader@example.com", "preset_id": "missing"},
        )
        assert status == 422
        assert result["error"]["code"] == "invalid_preset"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
