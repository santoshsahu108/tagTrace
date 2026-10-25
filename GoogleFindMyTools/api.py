"""HTTP API for the Android app and the React web UI.

A small ``http.server`` handler, CORS-enabled. The JSON contract is unchanged
from the pre-ORM collector:

    POST /login   {email, password}      -> {token, email, tag_setup, setup_required} | 401
    POST /logout  (Bearer)               -> {ok}
    POST /shares  (Bearer) {from,to,ttl_seconds} -> {token, from, to, expires_at}
    GET  /me      (Bearer)               -> {email, tag_setup}
    GET  /tag     (Bearer)               -> tag_setup (+ connect progress)
    POST /tag/connect (Bearer) {replace?} -> opens Chrome on this computer; poll GET /tag
    POST /tag/select (Bearer) {tag_id}   -> tag_setup (completed) | 4xx
    POST /tag/reset  (Bearer)            -> tag_setup (none)
    GET  /days    (Bearer)               -> {days: [{date, count}]}
    GET  /locations?since=EPOCH (Bearer) -> {points}          (Android app)
    GET  /locations?date=YYYY-MM-DD (Bearer) -> {points}      (web UI)
    GET  /shared?token=TOKEN             -> {points, from, to, expires_at} | 410
    GET  /health                         -> {ok, total}

/health and /shared are public; everything else requires a valid bearer token
and only ever sees the signed-in user's own tag and points. A module-level lock
serialises DB work, matching the original single-writer design; the Google
sign-in runs as a background job so requests never wait on it.

``tag_setup`` is ``{status: none|pending|completed|failed, setup_required,
google_email, tag_name, last_error, devices?, connect?}``. Setup errors are ``{error, code}`` with
codes from ``tag_provider.SetupError``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import auth
import repository
import shares
import tag_configs
from tag_provider import SetupError

# Setup error code -> HTTP status.
_SETUP_HTTP = {
    "missing_fields": 400,
    "unknown_tag": 400,
    "no_trackers": 422,
    "invalid_credentials": 422,
    "no_setup": 409,
    "no_browser": 503,
    "cancelled": 409,
    "already_configured": 409,
    "setup_in_progress": 409,
    "network": 502,
    "provider": 502,
    "timeout": 504,
}

# Request bodies are tiny JSON; refuse anything absurd.
_MAX_BODY = 64 * 1024

# Serialise all request handling, as the original collector did.
_lock = threading.Lock()


def make_handler() -> type[BaseHTTPRequestHandler]:
    """Build the request handler class."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, obj, code: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()

        def _bearer(self) -> str:
            header = self.headers.get("Authorization", "")
            return header[7:].strip() if header.startswith("Bearer ") else ""

        def _json_body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length > _MAX_BODY:
                return {}
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                return json.loads(raw or b"{}")
            except Exception:
                return {}

        # --- POST ------------------------------------------------------- #
        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/login":
                    return self._handle_login()
                if path == "/logout":
                    with _lock:
                        auth.logout(self._bearer())
                    return self._send({"ok": True})
                if path == "/shares":
                    return self._handle_create_share()
                if path in ("/tag/connect", "/tag/select", "/tag/reset"):
                    return self._handle_tag_post(path)
                self._send({"error": "not found"}, 404)
            except Exception as exc:  # last-resort guard
                self._send({"error": str(exc)}, 500)

        def _handle_login(self) -> None:
            data = self._json_body()
            with _lock:
                token = auth.login(data.get("email"), data.get("password"))
                user = auth.session_user(token) if token else None
                setup = tag_configs.get_status(user[0]) if user else None
            if not token or not user:
                return self._send({"error": "invalid credentials"}, 401)
            # Additive fields: older clients keep reading token/email only.
            self._send(
                {
                    "token": token,
                    "email": user[1],
                    "tag_setup": setup,
                    "setup_required": setup["setup_required"],
                }
            )

        def _handle_tag_post(self, path: str) -> None:
            with _lock:
                user = auth.session_user(self._bearer())
            if not user:
                return self._send({"error": "unauthorized"}, 401)
            user_id = user[0]
            data = self._json_body()
            try:
                if path == "/tag/connect":
                    # Returns at once; the sign-in runs in a background job.
                    result = tag_configs.start_connect(user_id, bool(data.get("replace")))
                elif path == "/tag/select":
                    with _lock:
                        result = tag_configs.select_tag(user_id, data.get("tag_id"))
                else:
                    with _lock:
                        result = tag_configs.reset(user_id)
            except SetupError as exc:
                return self._send(
                    {"error": exc.message, "code": exc.code},
                    _SETUP_HTTP.get(exc.code, 400),
                )
            self._send(result)

        def _handle_create_share(self) -> None:
            with _lock:
                user = auth.session_user(self._bearer())
                if not user:
                    return self._send({"error": "unauthorized"}, 401)
                user_id, email = user
                data = self._json_body()
                try:
                    ttl = int(data.get("ttl_seconds"))
                except (TypeError, ValueError):
                    ttl = 0
                date_from = (data.get("from") or "").strip()
                date_to = (data.get("to") or "").strip()
                if not date_from or not date_to:
                    return self._send({"error": "from and to dates are required"}, 400)
                try:
                    token, expires_at = shares.create_share(
                        date_from, date_to, ttl, email, user_id
                    )
                except ValueError as exc:
                    return self._send({"error": str(exc)}, 400)
            self._send(
                {
                    "token": token,
                    "from": date_from,
                    "to": date_to,
                    "expires_at": expires_at.isoformat(),
                }
            )

        # --- GET -------------------------------------------------------- #
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                with _lock:
                    if parsed.path == "/health":  # open liveness probe
                        return self._send(
                            {"ok": True, "total": repository.total_points()}
                        )
                    if parsed.path == "/shared":  # public, expiring share link
                        return self._handle_shared(query)
                    user = auth.session_user(self._bearer())
                    if not user:
                        return self._send({"error": "unauthorized"}, 401)
                    user_id, email = user
                    if parsed.path == "/me":
                        return self._send(
                            {"email": email, "tag_setup": tag_configs.get_status(user_id)}
                        )
                    if parsed.path == "/tag":
                        return self._send(tag_configs.get_status(user_id))
                    if parsed.path == "/days":
                        return self._send({"days": repository.days_with_data(user_id)})
                    if parsed.path == "/locations":
                        return self._handle_locations(user_id, query)
                self._send({"error": "not found"}, 404)
            except Exception as exc:  # last-resort guard
                self._send({"error": str(exc)}, 500)

        def _handle_shared(self, query: dict) -> None:
            share = shares.get_share(query.get("token", [""])[0])
            if not share or share["user_id"] is None:
                return self._send(
                    {"error": "This share link has expired or is invalid."}, 410
                )
            self._send(
                {
                    "points": repository.points_in_range(
                        share["user_id"], share["from"], share["to"]
                    ),
                    "from": share["from"],
                    "to": share["to"],
                    "expires_at": share["expires_at"],
                }
            )

        def _handle_locations(self, user_id: int, query: dict) -> None:
            if "date" in query:
                return self._send(
                    {"points": repository.points_for_day(user_id, query["date"][0])}
                )
            since = 0
            if "since" in query:
                try:
                    since = int(query["since"][0])
                except ValueError:
                    since = 0
            self._send({"points": repository.points_since(user_id, since)})

        def log_message(self, *args) -> None:
            pass  # keep the console quiet

    return Handler
