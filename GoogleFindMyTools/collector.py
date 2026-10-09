#!/usr/bin/env python3
"""Tag Trace collector — command-line entrypoint.

Fetches your JioTag 2's locations from Google's Find Hub network on a timer,
stores them in Postgres (via SQLAlchemy), and serves them to the Android app
and the React web UI over your home network.

Configuration comes from the environment / a local ``.env`` file — see
``.env.example``. Command-line flags override the configured defaults.

    python3 collector.py                       # use .env for everything
    python3 collector.py --tag Tag --interval 300 --port 8020
    python3 collector.py --add-user you@example.com --password 'secret'

This is a thin wrapper around GoogleFindMyTools (GPL-3.0), which does the
actual Google sign-in and decryption. Keep this file inside a checkout of that
repo, or point GFMT_DIR at one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import threading
import time

from config import get_settings
from db import init_database

_poll_lock = threading.Lock()


def _parse_args(settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tag Trace collector (SQLAlchemy)")
    parser.add_argument("--tag", default=settings.tag_name, help="part of the tag's name")
    parser.add_argument(
        "--interval", type=int, default=settings.poll_interval,
        help="seconds between fetches",
    )
    parser.add_argument(
        "--port", type=int, default=settings.http_port, help="HTTP port for app + web"
    )
    parser.add_argument(
        "--no-serve", action="store_true", help="only write to the DB, don't serve"
    )
    parser.add_argument(
        "--add-user", metavar="EMAIL", help="create/reset a login user, then exit"
    )
    parser.add_argument("--password", help="password for --add-user")
    return parser.parse_args()


def _poll_loop(interval: int, canonic_id: str, name: str) -> None:
    """Fetch, store, and report new points forever, surviving transient errors."""
    import findhub  # imported lazily so --add-user works without GFMT on the path
    import repository

    while True:
        try:
            new_points = findhub.collect_points(canonic_id, name)
            with _poll_lock:
                added = repository.insert_points(new_points, name)
                total = repository.total_points()
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            print(f"[{stamp}] fetched {len(new_points)}, {added} new, {total} total")
        except Exception as exc:  # keep polling across transient failures
            print(f"[poll] error: {exc}")
        time.sleep(interval)


def main() -> None:
    settings = get_settings()
    args = _parse_args(settings)

    init_database()

    if args.add_user:
        if not args.password:
            raise SystemExit("--add-user requires --password")
        import auth

        auth.add_user(args.add_user, args.password)
        print(f"[auth] user {args.add_user.strip().lower()!r} ready")
        return

    import findhub
    import repository

    print(f"[db] connected, {repository.total_points()} points stored")

    name, canonic_id = findhub.find_canonic_id(args.tag)
    print(f"Tracking {name!r} every {args.interval}s")

    poller = threading.Thread(
        target=_poll_loop, args=(args.interval, canonic_id, name), daemon=True
    )
    poller.start()

    if args.no_serve:
        poller.join()
        return

    from http.server import ThreadingHTTPServer

    from api import make_handler

    server = ThreadingHTTPServer((settings.http_host, args.port), make_handler())
    print(
        f"Serving http://{settings.http_host}:{args.port}  "
        "(/login, /locations, /days, /shares, /shared, /health)  Ctrl-C to stop"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
