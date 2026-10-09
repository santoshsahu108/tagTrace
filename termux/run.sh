#!/data/data/com.termux/files/usr/bin/bash
# Runs the Tag Trace collector on the phone and serves it to the app on
# http://127.0.0.1:8020. Settings (DATABASE_URL, TAG_NAME, ...) come from
# GoogleFindMyTools/.env; Google tokens from GoogleFindMyTools/Auth/secrets.json.
# See SETUP-TERMUX.md.
set -e
REPO="${TAGTRACE_DIR:-$HOME/tagTrace}"
termux-wake-lock 2>/dev/null || true   # keep running with the screen off
cd "$REPO/GoogleFindMyTools"
exec python collector.py
