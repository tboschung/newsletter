from __future__ import annotations

import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import Settings
from .storage import Storage


LOGGER = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_BODY_BYTES = 16_384


def _public_presets(settings: Settings) -> list[dict[str, object]]:
    return [
        {
            "id": preset.preset_id,
            "name": preset.name,
            "description": preset.description,
            "content": sorted(preset.content),
        }
        for preset in settings.presets
    ]


def make_handler(settings: Settings, index_path: Path) -> type[BaseHTTPRequestHandler]:
    preset_ids = {preset.preset_id for preset in settings.presets}

    class NewsletterHandler(BaseHTTPRequestHandler):
        server_version = "MorningDigest/1"

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )

        def _json(self, status: HTTPStatus, payload: dict[str, Any] | list[Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: HTTPStatus, code: str, message: str) -> None:
            self._json(status, {"error": {"code": code, "message": message}})

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = self.path.partition("?")[0]
            if path == "/api/presets":
                self._json(HTTPStatus.OK, {"presets": _public_presets(settings)})
                return
            if path not in {"/", "/index.html"}:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "That endpoint does not exist.")
                return
            try:
                body = index_path.read_bytes()
            except FileNotFoundError:
                self._error(HTTPStatus.NOT_FOUND, "page_missing", "The signup page is unavailable.")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path.partition("?")[0] != "/api/subscribers":
                self._error(HTTPStatus.NOT_FOUND, "not_found", "That endpoint does not exist.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY_BYTES:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_body", "Send a small JSON request body.")
                return
            if self.headers.get_content_type() != "application/json":
                self._error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "invalid_content_type",
                    "Use application/json.",
                )
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "The request is not valid JSON.")
                return
            if not isinstance(payload, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_body", "Send a JSON object.")
                return
            email = str(payload.get("email", "")).strip().casefold()
            preset_id = str(payload.get("preset_id", "")).strip()
            if len(email) > 254 or not EMAIL_RE.fullmatch(email):
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_email",
                    "Enter a valid email address.",
                )
                return
            if preset_id not in preset_ids:
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_preset",
                    "Choose one of the available digests.",
                )
                return
            storage = Storage(settings.database_path)
            try:
                _, created = storage.upsert_subscriber(email, preset_id)
            finally:
                storage.close()
            self._json(
                HTTPStatus.CREATED if created else HTTPStatus.OK,
                {
                    "status": "subscribed",
                    "message": "You're on the list. Your selected digest will arrive each morning.",
                },
            )

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("%s - %s", self.address_string(), format % args)

    return NewsletterHandler


def serve(settings: Settings, host: str, port: int, index_path: Path) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(settings, index_path))
    LOGGER.info("Signup server listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Signup server stopped")
    finally:
        server.server_close()
