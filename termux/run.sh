#!/data/data/com.termux/files/usr/bin/bash
# Runs the collector on the phone and serves it to the app on localhost.
# Expects GoogleFindMyTools cloned next to this, with collector.py and a
# valid Auth/secrets.json copied in (see SETUP-TERMUX.md).
set -e
GFMT="${GFMT_DIR:-$HOME/GoogleFindMyTools}"
termux-wake-lock 2>/dev/null || true   # keep running with the screen off
cd "$GFMT"
exec python collector.py --tag "JioTag" --interval 900 --port 8020 --out locations.json
