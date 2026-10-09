"""HTTP API for the Android app and the React web UI.

A small ``http.server`` handler, CORS-enabled. The JSON contract is unchanged
from the pre-ORM collector:

    POST /login   {email, password}      -> {token, email} | 401
    POST /logout  (Bearer)               -> {ok}
    POST /shares  (Bearer) {from,to,ttl_seconds} -> {token, from, to, expires_at}
    GET  /me      (Bearer)               -> {email}
    GET  /days    (Bearer)               -> {days: [{date, count}]}
    GET  /locations?since=EPOCH (Bearer) -> {points}          (Android app)
    GET  /locations?date=YYYY-MM-DD (Bearer) -> {points}      (web UI)
    GET  /shared?token=TOKEN             -> {points, from, to, expires_at} | 410
    GET  /health                         -> {ok, total}

/health and /shared are public; everything else requires a valid bearer token.
A module-level lock serialises DB work, matching the original single-writer
design.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import auth
import repository
import shares

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
                self._send({"error": "not found"}, 404)
            except Exception as exc:  # last-resort guard
                self._send({"error": str(exc)}, 500)

        def _handle_login(self) -> None:
            data = self._json_body()
            with _lock:
                token = auth.login(data.get("email"), data.get("password"))
            if not token:
                return self._send({"error": "invalid credentials"}, 401)
            self._send(
                {"token": token, "email": (data.get("email") or "").strip().lower()}
            )

        def _handle_create_share(self) -> None:
            with _lock:
                email = auth.session_email(self._bearer())
                if not email:
                    return self._send({"error": "unauthorized"}, 401)
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
                        date_from, date_to, ttl, email
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
                    email = auth.session_email(self._bearer())
                    if not email:
                        return self._send({"error": "unauthorized"}, 401)
                    if parsed.path == "/me":
                        return self._send({"email": email})
                    if parsed.path == "/days":
                        return self._send({"days": repository.days_with_data()})
                    if parsed.path == "/locations":
                        return self._handle_locations(query)
                self._send({"error": "not found"}, 404)
            except Exception as exc:  # last-resort guard
                self._send({"error": str(exc)}, 500)

        def _handle_shared(self, query: dict) -> None:
            share = shares.get_share(query.get("token", [""])[0])
            if not share:
                return self._send(
                    {"error": "This share link has expired or is invalid."}, 410
                )
            self._send(
                {
                    "points": repository.points_in_range(share["from"], share["to"]),
                    "from": share["from"],
                    "to": share["to"],
                    "expires_at": share["expires_at"],
                }
            )

        def _handle_locations(self, query: dict) -> None:
            if "date" in query:
                return self._send(
                    {"points": repository.points_for_day(query["date"][0])}
                )
            since = 0
            if "since" in query:
                try:
                    since = int(query["since"][0])
                except ValueError:
                    since = 0
            self._send({"points": repository.points_since(since)})

        def log_message(self, *args) -> None:
            pass  # keep the console quiet

    return Handler
