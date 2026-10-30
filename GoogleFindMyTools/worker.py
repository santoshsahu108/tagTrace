"""Per-user poller: one process per completed tag configuration.

Runs in its own process (see ``tag_provider`` for why) with exactly one user's
credentials loaded in memory. It has no request context: the user is the one
that owns ``config_id``, and every point it stores is written with that
``user_id``.
"""

from __future__ import annotations

import datetime as dt
import time

from tag_provider import classify_error

# After this many polls in a row with no FCM answer, exit so the supervisor
# starts a fresh process. The FCM push client gives up for good after a few
# connection errors (network drop, sleep), and from then on every poll times
# out; a new process opens a new FCM connection.
_MAX_FCM_TIMEOUTS = 3


def _print_points(prefix: str, points: list[dict]) -> None:
    """Print every field of each fetched point, oldest first."""
    for p in sorted(points, key=lambda p: p["t"]):
        when = dt.datetime.fromtimestamp(p["t"]).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{prefix}    {when}  lat {p['lat']:.7f}  lng {p['lng']:.7f}  "
            f"alt {p.get('alt', '')}m  acc {p['acc']}m  src {p['src'] or '-'}  "
            f"status {p.get('status', '')}  own {p.get('own', '')}\n"
            f"{prefix}      https://www.google.com/maps/search/?api=1&query={p['lat']},{p['lng']}",
            flush=True,
        )


def run_worker(config_id: int, interval: int) -> None:
    """Poll one user's tag forever; exit if its Google login stops working."""
    import tag_configs
    from tag_provider import prepare_process

    config = tag_configs.load_for_worker(config_id)
    if config is None:
        return
    prefix = f"[user {config['user_id']}]"
    prepare_process(
        config["credentials"],
        lambda store: tag_configs.save_credentials(config_id, dict(store)),
    )

    import findhub  # GFMT imports happen only after credentials are in place
    import repository

    print(f"{prefix} tracking {config['tag_name']!r} every {interval}s", flush=True)
    timeouts = 0
    while True:
        try:
            points = findhub.collect_points(config["tag_id"], config["tag_name"])
            added = repository.insert_points(
                points, config["tag_name"], config["user_id"], config_id
            )
            total = repository.total_points(config["user_id"])
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            print(f"{prefix} [{stamp}] fetched {len(points)}, {added} new, "
                  f"{total} total", flush=True)
            _print_points(prefix, points)
            timeouts = 0
        except TimeoutError as exc:
            timeouts += 1
            print(f"{prefix} [poll] {exc} ({timeouts}/{_MAX_FCM_TIMEOUTS})", flush=True)
            if timeouts >= _MAX_FCM_TIMEOUTS:
                print(f"{prefix} FCM connection looks dead; restarting worker",
                      flush=True)
                return
        except Exception as exc:
            code, message = classify_error(exc)
            if code == "invalid_credentials":
                tag_configs.mark_failed(config_id, message)
                print(f"{prefix} Google login no longer valid; tag marked failed "
                      "(user must reconnect)", flush=True)
                return
            print(f"{prefix} [poll] error: {exc}", flush=True)
        time.sleep(interval)
