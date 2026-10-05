from __future__ import annotations

import json
import logging
import re
from http.cookies import SimpleCookie
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .storage import Storage
from .emailer import build_confirmation_message, send_email


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
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _cookies(self) -> dict[str, str]:
            cookie = SimpleCookie()
            try: cookie.load(self.headers.get("Cookie", ""))
            except Exception: return {}
            return {key: morsel.value for key, morsel in cookie.items()}

        def _session(self, storage: Storage, payload: dict[str, Any] | None = None, *, mutate: bool = False):
            cookies = self._cookies()
            csrf = None
            if mutate:
                payload = payload or {}
                csrf = str(payload.get("csrf_token", "") or self.headers.get("X-CSRF-Token", ""))
                if not csrf or csrf != cookies.get("digest_csrf", ""):
                    return None
            return storage.session_subscriber(cookies.get("digest_session", ""), csrf=csrf)

        def _html(self, status: HTTPStatus, body: str, *, no_store: bool = False) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            if no_store:
                self.send_header("Cache-Control", "no-store")
            self._security_headers(); self.end_headers(); self.wfile.write(encoded)

        def _error(self, status: HTTPStatus, code: str, message: str) -> None:
            self._json(status, {"error": {"code": code, "message": message}})

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlsplit(self.path); path = parsed.path
            if path == "/api/presets":
                self._json(HTTPStatus.OK, {"presets": _public_presets(settings)})
                return
            if path == "/confirm":
                token = parse_qs(parsed.query).get("token", [""])[0]
                storage = Storage(settings.database_path)
                try: valid = bool(token) and storage.inspect_confirmation(token)
                finally: storage.close()
                if valid:
                    body = ("<!doctype html><title>Confirm subscription</title><h1>Confirm your subscription</h1>"
                            "<p>Complete this step to receive your selected digest.</p>"
                            f"<form method='post' action='api/subscription/confirm'><input type='hidden' name='token' value='{escape(token)}'>"
                            "<button type='submit'>Confirm subscription</button></form>")
                else:
                    body = "<!doctype html><title>Link unavailable</title><h1>This confirmation link is invalid or expired.</h1>"
                self._html(HTTPStatus.OK, body, no_store=True); return
            if path == "/manage":
                token = parse_qs(parsed.query).get("token", [""])[0]
                storage = Storage(settings.database_path)
                try:
                    if token:
                        subscriber = storage.inspect_token(token, "management")
                        body = ("<!doctype html><title>Manage subscription</title><h1>Manage your subscription</h1>"
                                "<p>Continue to view or change your preferences.</p>"
                                f"<form method='post' action='api/subscription/session'><input type='hidden' name='token' value='{escape(token)}'>"
                                "<button type='submit'>Continue</button></form>") if subscriber else "<!doctype html><title>Link unavailable</title><h1>This management link is invalid or expired.</h1>"
                    else:
                        subscriber = self._session(storage)
                        if subscriber is None or subscriber["status"] == "disabled":
                            body = "<!doctype html><title>Access required</title><h1>Your management session is invalid or expired.</h1>"
                        elif subscriber["preset_id"] not in preset_ids:
                            body = ("<!doctype html><title>Configuration error</title>"
                                    "<h1>Your saved preset is no longer configured.</h1>"
                                    "<p>Please contact the newsletter administrator.</p>")
                        else:
                            csrf = escape(self._cookies().get("digest_csrf", ""))
                            options = "".join(f"<option value='{escape(p.preset_id)}'{' selected' if p.preset_id == subscriber['preset_id'] else ''}>{escape(p.name)}</option>" for p in settings.presets)
                            body = (f"<!doctype html><title>Manage subscription</title><h1>Manage your subscription</h1><p>{escape(subscriber['email'])}</p>"
                                    f"<p>Status: {escape(subscriber['status'])}</p><form method='post' action='api/subscription/preferences'><input type='hidden' name='csrf_token' value='{csrf}'><select name='preset_id'>{options}</select><button type='submit'>Save preset</button></form>"
                                    f"<form method='post' action='api/subscription/resubscribe'><input type='hidden' name='csrf_token' value='{csrf}'><button type='submit'>Resubscribe</button></form>")
                finally: storage.close()
                self._html(HTTPStatus.OK, body, no_store=True); return
            if path == "/unsubscribe":
                token = parse_qs(parsed.query).get("token", [""])[0]
                storage = Storage(settings.database_path)
                try: subscriber = storage.inspect_token(token, "unsubscribe") if token else None
                finally: storage.close()
                body = ("<!doctype html><title>Unsubscribe</title><h1>Unsubscribe?</h1><p>You will stop receiving this digest.</p>"
                        f"<form method='post' action='api/subscription/unsubscribe'><input type='hidden' name='token' value='{escape(token)}'><button type='submit'>Unsubscribe</button></form>") if subscriber else "<!doctype html><title>Link unavailable</title><h1>This unsubscribe link is invalid or expired.</h1>"
                self._html(HTTPStatus.OK, body, no_store=True); return
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
            path = urlsplit(self.path).path
            if path not in {"/api/subscribers", "/api/subscription/confirm", "/api/subscription/confirmation/resend", "/api/subscription/session", "/api/subscription/preferences", "/api/subscription/unsubscribe", "/api/subscription/resubscribe"}:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "That endpoint does not exist.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY_BYTES:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_body", "Send a small JSON request body.")
                return
            content_type = self.headers.get_content_type()
            if content_type not in {"application/json", "application/x-www-form-urlencoded"}:
                self._error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "invalid_content_type",
                    "Use application/json.",
                )
                return
            try:
                raw = self.rfile.read(length)
                payload = json.loads(raw) if content_type == "application/json" else {
                    key: values[0] for key, values in parse_qs(raw.decode()).items()
                }
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "The request is not valid JSON.")
                return
            if not isinstance(payload, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_body", "Send a JSON object.")
                return
            if path == "/api/subscription/session":
                storage = Storage(settings.database_path)
                try: credentials = storage.create_management_session(str(payload.get("token", "")))
                finally: storage.close()
                if not credentials:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_token", "The management token is invalid or expired."); return
                session, csrf = credentials
                body = b'<!doctype html><title>Access granted</title><h1>Access granted</h1><p><a href="../../manage">Manage subscription</a></p>'
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", f"digest_session={session}; Max-Age=1800; Path=/; Secure; HttpOnly; SameSite=Strict")
                self.send_header("Set-Cookie", f"digest_csrf={csrf}; Max-Age=1800; Path=/; Secure; SameSite=Strict")
                self.send_header("Cache-Control", "no-store"); self._security_headers(); self.end_headers(); self.wfile.write(body); return
            if path in {"/api/subscription/preferences", "/api/subscription/resubscribe"}:
                storage = Storage(settings.database_path)
                try:
                    subscriber = self._session(storage, payload, mutate=True)
                    if subscriber is None:
                        self._error(HTTPStatus.FORBIDDEN, "invalid_csrf", "A valid management session and CSRF token are required."); return
                    if path.endswith("preferences"):
                        preset_id = str(payload.get("preset_id", ""))
                        if preset_id not in preset_ids:
                            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_preset", "Choose one of the available digests."); return
                        if not storage.update_preset(self._cookies().get("digest_session", ""), str(payload.get("csrf_token", "") or self.headers.get("X-CSRF-Token", "")), preset_id):
                            self._error(HTTPStatus.FORBIDDEN, "session_invalid", "The management session is invalid."); return
                        result = {"status": "updated", "preset_id": preset_id}
                    else:
                        subscriber, token = storage.resubscribe(self._cookies().get("digest_session", ""), str(payload.get("csrf_token", "") or self.headers.get("X-CSRF-Token", "")))
                        if subscriber is None or subscriber["status"] == "disabled":
                            self._error(HTTPStatus.FORBIDDEN, "not_allowed", "This subscription cannot be reactivated."); return
                        if token: self._send_confirmation(subscriber["email"], token)
                        result = {"status": "pending"}
                finally: storage.close()
                self._json(HTTPStatus.OK, result); return
            if path == "/api/subscription/unsubscribe":
                token = str(payload.get("token", "")) or parse_qs(urlsplit(self.path).query).get("token", [""])[0]
                storage = Storage(settings.database_path)
                try: changed = storage.unsubscribe(token)
                finally: storage.close()
                if not changed: self._error(HTTPStatus.BAD_REQUEST, "invalid_token", "The unsubscribe token is invalid or expired."); return
                self._json(HTTPStatus.OK, {"status": "unsubscribed"}); return
            if path == "/api/subscription/confirm":
                token = str(payload.get("token", ""))
                storage = Storage(settings.database_path)
                try: confirmed = bool(token) and storage.confirm(token)
                finally: storage.close()
                if content_type == "application/x-www-form-urlencoded":
                    message = "Subscription confirmed." if confirmed else "This confirmation link is invalid or expired."
                    self._html(HTTPStatus.OK, f"<!doctype html><title>Subscription</title><h1>{message}</h1>", no_store=True)
                else:
                    self._json(HTTPStatus.OK if confirmed else HTTPStatus.BAD_REQUEST,
                               {"status": "confirmed"} if confirmed else {"error": {"code": "invalid_token", "message": "The confirmation token is invalid or expired."}})
                return
            email = str(payload.get("email", "")).strip().casefold()
            if path == "/api/subscription/confirmation/resend":
                if len(email) <= 254 and EMAIL_RE.fullmatch(email):
                    storage = Storage(settings.database_path)
                    try: subscriber, token = storage.resend_confirmation(email)
                    finally: storage.close()
                    if subscriber is not None and token:
                        self._send_confirmation(subscriber["email"], token)
                self._accepted(); return
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
                subscriber, token = storage.request_confirmation(email, preset_id)
            finally:
                storage.close()
            if token:
                self._send_confirmation(subscriber["email"], token)
            self._accepted()

        def _accepted(self) -> None:
            self._json(HTTPStatus.ACCEPTED, {"status": "pending", "message": "Check your inbox for a confirmation link. If the address is eligible, it will arrive shortly."})

        def _send_confirmation(self, email: str, token: str) -> None:
            username = getattr(settings, "smtp_username", "")
            password = getattr(settings, "smtp_app_password", "")
            public_url = getattr(settings, "public_url", "")
            if not username or not password or not public_url:
                LOGGER.error("Confirmation email configuration is incomplete")
                return
            try:
                send_email(build_confirmation_message(username, email, public_url, token), username, password)
            except Exception as exc:
                LOGGER.error("Confirmation email failed: %s", " ".join(str(exc).split())[:500])

        def log_message(self, format: str, *args: object) -> None:
            message = format % args
            message = re.sub(r"(GET|POST) ([^ ?]+)\?[^ ]+", r"\1 \2?[redacted]", message)
            LOGGER.info("%s - %s", self.address_string(), message)

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
