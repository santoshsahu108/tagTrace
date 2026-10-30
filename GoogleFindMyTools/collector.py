#!/usr/bin/env python3
"""Tag Trace collector — command-line entrypoint.

Fetches each user's Find Hub tag locations on a timer, stores them in
Postgres (via SQLAlchemy), and serves them to the Android app and the React
web UI.

Each signed-in user configures their own tag (see ``tag_configs``); the
collector runs one isolated worker process per completed configuration and
starts/stops workers as users finish setup, reset, or lose their Google login.

Configuration comes from the environment / a local ``.env`` file — see
``.env.example``. Command-line flags override the configured defaults.

    python3 collector.py                       # use .env for everything
    python3 collector.py --interval 300 --port 8020
    python3 collector.py --add-user you@example.com --password 'secret'
    python3 collector.py --generate-key        # value for TAG_CREDENTIALS_KEY
    python3 collector.py --setup-only          # web/API for Connect with Google only

This is a thin wrapper around GoogleFindMyTools (GPL-3.0), which does the
actual Google sign-in and decryption.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import threading
import time

from config import get_settings
from db import init_database

# Seconds between checks for new / reset / failed tag configurations.
_SUPERVISE_EVERY = 15
# Wait before restarting a worker that exited (e.g. crash), per config.
_RESTART_BACKOFF = 60


def _parse_args(settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tag Trace collector (SQLAlchemy)")
    parser.add_argument(
        "--tag", default=settings.tag_name,
        help="legacy import only: part of the tag's name in secrets.json's account",
    )
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
    parser.add_argument(
        "--setup-only", action="store_true",
        help="serve the API for tag setup only: no tracking, no legacy import "
        "(run on a computer with Chrome while production tracks elsewhere)",
    )
    parser.add_argument(
        "--generate-key", action="store_true",
        help="print a new TAG_CREDENTIALS_KEY, then exit",
    )
    return parser.parse_args()


def _supervise(interval: int) -> None:
    """Keep exactly one worker process alive per completed tag configuration."""
    import tag_configs
    import worker

    ctx = mp.get_context("spawn")
    running: dict[int, mp.Process] = {}
    exited_at: dict[int, float] = {}
    while True:
        try:
            wanted = {c["id"]: c for c in tag_configs.completed_configs()}
        except Exception as exc:  # DB hiccup: keep current workers, retry
            print(f"[supervisor] can't read tag configs: {exc}")
            wanted = None

        if wanted is not None:
            for config_id in list(running):
                proc = running[config_id]
                if config_id not in wanted:  # reset, replaced, or failed
                    if proc.is_alive():
                        proc.terminate()
                    proc.join(5)
                    del running[config_id]
                    print(f"[supervisor] stopped worker for tag config {config_id}")
                elif not proc.is_alive():
                    del running[config_id]
                    exited_at[config_id] = time.monotonic()

            for config_id, config in wanted.items():
                if config_id in running:
                    continue
                if time.monotonic() - exited_at.get(config_id, -1e9) < _RESTART_BACKOFF:
                    continue
                proc = ctx.Process(
                    target=worker.run_worker, args=(config_id, interval), daemon=True
                )
                proc.start()
                running[config_id] = proc
                print(f"[supervisor] started worker for user {config['user_id']} "
                      f"({config['tag_name']!r})")
        time.sleep(_SUPERVISE_EVERY)


def main() -> None:
    settings = get_settings()
    args = _parse_args(settings)

    if args.generate_key:
        from crypto import generate_key

        print(generate_key())
        return

    init_database()

    if args.add_user:
        if not args.password:
            raise SystemExit("--add-user requires --password")
        import auth

        auth.add_user(args.add_user, args.password)
        print(f"[auth] user {args.add_user.strip().lower()!r} ready")
        return

    import repository
    import tag_configs
    from crypto import CredentialKeyError, _fernet

    try:
        _fernet()  # fail fast: per-user credentials can't be stored without it
    except CredentialKeyError as exc:
        raise SystemExit(f"[config] {exc}")

    print(f"[db] connected, {repository.total_points()} points stored")
    if args.setup_only:
        # Production polls elsewhere against the same database; this instance
        # only hosts Connect with Google, so it must not start any worker.
        print("[setup-only] tracking is off here; finished setups are picked "
              "up by the collector running elsewhere")
    else:
        tag_configs.import_legacy(args.tag)
        supervisor = threading.Thread(
            target=_supervise, args=(args.interval,), daemon=True
        )
        supervisor.start()

        if args.no_serve:
            supervisor.join()
            return

    from http.server import ThreadingHTTPServer

    from api import make_handler

    server = ThreadingHTTPServer((settings.http_host, args.port), make_handler())
    print(
        f"Serving http://{settings.http_host}:{args.port}  "
        "(/login, /tag, /locations, /days, /shares, /shared, /health)  Ctrl-C to stop"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
