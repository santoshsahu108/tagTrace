#!/usr/bin/env python3
"""Mock feeder for testing the app without the Google pipeline.

Serves a handful of sample points for *today* so you can see the day list and
the map trace immediately. Run it on your Mac, then point the app's
"Over Wi-Fi" setting at this machine's address.

    python3 tools/mock_server.py            # serves on port 8020
    python3 tools/mock_server.py --port 9000

The app reads  http://<this-mac-ip>:8020/locations
Find your Mac's IP with:  ipconfig getifaddr en0
"""
import argparse
import datetime
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# A short walk across the day near central Bengaluru (just sample coordinates).
_BASE_LAT, _BASE_LNG = 12.9716, 77.5946
_STEPS = [
    (0.0000, 0.0000, 8, "own"),
    (0.0020, 0.0015, 20, "crowdsourced"),
    (0.0041, 0.0032, 35, "crowdsourced"),
    (0.0055, 0.0061, 18, "crowdsourced"),
    (0.0048, 0.0090, 25, "aggregated"),
    (0.0030, 0.0120, 15, "crowdsourced"),
]


def _sample_points():
    now = datetime.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Spread the points from ~8am to now.
    start = midnight + datetime.timedelta(hours=8)
    span = max((now - start).total_seconds(), 3600)
    pts = []
    for i, (dlat, dlng, acc, src) in enumerate(_STEPS):
        t = start + datetime.timedelta(seconds=span * i / (len(_STEPS) - 1))
        if t > now:
            break
        pts.append({
            "t": int(t.timestamp()),
            "lat": round(_BASE_LAT + dlat, 7),
            "lng": round(_BASE_LNG + dlng, 7),
            "acc": float(acc),
            "src": src,
        })
    return pts


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
        points = [p for p in _sample_points() if p["t"] > since]
        body = json.dumps({"points": points}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        print(f"  {self.address_string()} -> {self.path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8020)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"Mock feeder on http://0.0.0.0:{args.port}/locations  (Ctrl-C to stop)")
    print("Point the app's 'Over Wi-Fi' setting at this Mac's LAN IP + port.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
