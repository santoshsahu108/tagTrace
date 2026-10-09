#!/usr/bin/env python3
"""Tag-trace collector.

Fetches your JioTag 2's locations from Google's Find Hub network on a timer,
keeps them in a JSON file (deduplicated), and serves them to the Tag Trace
Android app over your home network.

It is a thin wrapper around GoogleFindMyTools
(https://github.com/leonboe1/GoogleFindMyTools, GPL-3.0), which does the actual
Google sign-in and decryption. Put this file inside a clone of that repo, or
set GFMT_DIR below to point at one.

Usage:
    python3 collector.py --tag "JioTag" --interval 900 --port 8020 \
        --out locations.json

The app then reads from http://<this-machine-ip>:8020/locations
"""

import argparse
import datetime
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Point this at your GoogleFindMyTools checkout if this file isn't inside it.
GFMT_DIR = os.environ.get("GFMT_DIR", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GFMT_DIR)

# These imports come from GoogleFindMyTools.
from NovaApi.ListDevices.nbe_list_devices import request_device_list  # noqa: E402
from NovaApi.ExecuteAction.LocateTracker.location_request import (  # noqa: E402
    get_location_data_for_device,
)
from ProtoDecoders.decoder import parse_device_list_protobuf, get_canonic_ids  # noqa: E402
import NovaApi.ExecuteAction.LocateTracker.decrypt_locations as gfmt_decrypt  # noqa: E402
from ProtoDecoders import DeviceUpdate_pb2, Common_pb2  # noqa: E402

_lock = threading.Lock()


def _find_canonic_id(tag_name):
    """Return the (name, canonic_id) whose name contains tag_name, else list all."""
    device_list = parse_device_list_protobuf(request_device_list())
    ids = get_canonic_ids(device_list)
    for name, cid in ids:
        if tag_name.lower() in name.lower():
            return name, cid
    raise SystemExit(
        "Tag %r not found. Trackers on your account:\n  %s"
        % (tag_name, "\n  ".join(n for n, _ in ids) or "(none)")
    )


def _collect_points(canonic_id, name):
    """Ask Find Hub for this tag and return a list of structured points.

    Reuses GoogleFindMyTools' own decryption; we only reshape the result into
    plain dicts instead of printing it.
    """
    captured = {}
    original = gfmt_decrypt.decrypt_location_response_locations

    def capture(device_update):
        captured["update"] = device_update
        try:
            return original(device_update)  # keeps GFMT's own validation path
        except Exception:
            return None

    gfmt_decrypt.decrypt_location_response_locations = capture
    try:
        get_location_data_for_device(canonic_id, name)
    finally:
        gfmt_decrypt.decrypt_location_response_locations = original

    update = captured.get("update")
    if update is None:
        return []
    return _decode_update(update)


def _decode_update(update):
    reg = update.deviceMetadata.information.deviceRegistration
    identity_key = gfmt_decrypt.retrieve_identity_key(reg)
    is_mcu = gfmt_decrypt.is_mcu_tracker(reg)
    reports = (
        update.deviceMetadata.information.locationInformation.reports
        .recentLocationAndNetworkLocations
    )

    locs = list(reports.networkLocations)
    times = list(reports.networkLocationTimestamps)
    if reports.HasField("recentLocation"):
        locs.append(reports.recentLocation)
        times.append(reports.recentLocationTimestamp)

    import hashlib
    status_names = {
        Common_pb2.Status.LAST_KNOWN: "last_known",
        Common_pb2.Status.CROWDSOURCED: "crowdsourced",
        Common_pb2.Status.AGGREGATED: "aggregated",
    }

    points = []
    for loc, t in zip(locs, times):
        if loc.status == Common_pb2.Status.SEMANTIC:
            continue  # named place, no coordinates
        enc = loc.geoLocation.encryptedReport
        try:
            if enc.publicKeyRandom == b"":  # own report
                key_hash = hashlib.sha256(identity_key).digest()
                plain = gfmt_decrypt.decrypt_aes_gcm(key_hash, enc.encryptedLocation)
            else:
                offset = 0 if is_mcu else loc.geoLocation.deviceTimeOffset
                plain = gfmt_decrypt.decrypt(
                    identity_key, enc.encryptedLocation, enc.publicKeyRandom, offset
                )
        except Exception:
            continue
        proto = DeviceUpdate_pb2.Location()
        proto.ParseFromString(plain)
        points.append({
            "t": int(t.seconds),
            "lat": proto.latitude / 1e7,
            "lng": proto.longitude / 1e7,
            "acc": round(float(loc.geoLocation.accuracy), 1),
            "src": "own" if enc.isOwnReport else status_names.get(loc.status, ""),
        })
    return points


def _load(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(path, points):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(points, f)
    os.replace(tmp, path)


def _merge(existing, new):
    seen = {(p["t"], p["lat"], p["lng"]) for p in existing}
    added = 0
    for p in new:
        k = (p["t"], p["lat"], p["lng"])
        if k not in seen:
            seen.add(k)
            existing.append(p)
            added += 1
    existing.sort(key=lambda p: p["t"])
    return added


def _poll_loop(args, canonic_id, name):
    while True:
        try:
            new = _collect_points(canonic_id, name)
            with _lock:
                data = _load(args.out)
                added = _merge(data, new)
                _save(args.out, data)
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{stamp}] fetched {len(new)}, {added} new, {len(data)} total")
        except Exception as e:  # keep the loop alive across transient errors
            print(f"[poll] error: {e}")
        time.sleep(args.interval)


def _make_handler(path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/locations":
                self.send_error(404)
                return
            since = 0
            q = parse_qs(parsed.query)
            if "since" in q:
                try:
                    since = int(q["since"][0])
                except ValueError:
                    pass
            with _lock:
                data = _load(path)
            points = [p for p in data if p["t"] > since]
            body = json.dumps({"points": points}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass  # quiet

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Tag Trace collector")
    ap.add_argument("--tag", default="JioTag", help="part of the tag's name")
    ap.add_argument("--interval", type=int, default=900, help="seconds between fetches")
    ap.add_argument("--port", type=int, default=8020, help="HTTP port for the app")
    ap.add_argument("--out", default="locations.json", help="JSON store path")
    ap.add_argument("--no-serve", action="store_true", help="only write the file")
    args = ap.parse_args()

    name, canonic_id = _find_canonic_id(args.tag)
    print(f"Tracking {name!r}")

    poller = threading.Thread(target=_poll_loop, args=(args, canonic_id, name),
                              daemon=True)
    poller.start()

    if args.no_serve:
        poller.join()
        return

    server = ThreadingHTTPServer(("0.0.0.0", args.port), _make_handler(args.out))
    print(f"Serving http://0.0.0.0:{args.port}/locations  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
