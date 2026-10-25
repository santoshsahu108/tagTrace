# Tag Trace — next features plan

What exists today: a collector polls Find Hub every 5 min into Neon (PostGIS),
a web UI shows a day's trace (playback, colour by time, share links), and the
Android app syncs per day. The biggest gaps are **knowing when it breaks**
(it was silently down Oct 15–21) and **the end-of-day trace you originally
asked for**.

Order below = recommended build order. Each item is small enough for one
change; nothing is built until you approve it.

---

## Phase 1 — Reliability (so it never silently stops again)

| # | Feature | What you get | Effort |
|---|---|---|---|
| 1 | **Stale-data alert** | Phone notification (ntfy.sh or Telegram, free) when no new point for N hours, or when fetches keep failing | S |
| 2 | **Richer `/health`** | `/health` shows last fetch time, last point time, last error, consecutive failures — check from anywhere | S |
| 3 | **Single-collector lock** | Second collector refuses to start (DB advisory lock), so Mac + phone can't fight over the Google push | S |
| 4 | **Token-expiry detection** | Clear message + alert when Google login needs redoing, instead of timeouts | S |

## Phase 2 — End-of-day trace (the original goal)

| # | Feature | What you get | Effort |
|---|---|---|---|
| 5 | **Daily summary report** | At a set time (e.g. 23:30) a message with: distance travelled (PostGIS), first/last seen, number of points, places stopped, link to the day's map | M |
| 6 | **Stops / places** | Detect where the tag stayed ≥ 10 min (cluster nearby points), with arrive/leave time; shown on map and in the report | M |
| 7 | **Clean trace** | Drop low-accuracy jumps (e.g. acc > 300 m or impossible speed) so lines don't zig-zag; toggle "raw / cleaned" in UI | S |

## Phase 3 — Places & alerts

| # | Feature | What you get | Effort |
|---|---|---|---|
| 8 | **Geofences** | Draw zones (Home, Office) on the map; log enter/exit events using PostGIS `ST_DWithin` | M |
| 9 | **Zone alerts** | Notification on enter/exit of a chosen zone ("Tag left Home 09:12") | S (after 8) |
| 10 | **Name places** | Label frequent stops; report says "Home 8h, Office 6h" instead of coordinates | S (after 6) |

## Phase 4 — History & data

| # | Feature | What you get | Effort |
|---|---|---|---|
| 11 | **Export** | Download a day/range as GPX, KML (Google Earth) or CSV | S |
| 12 | **Range & heatmap view** | Pick a week/month; heatmap of where the tag spends time | M |
| 13 | **Stats page** | Distance per day chart, active hours, most visited places | M |
| 14 | **Retention** | Optional auto-delete of points older than N months (privacy, DB size) | S |

## Phase 5 — Polish & ops

| # | Feature | What you get | Effort |
|---|---|---|---|
| 15 | **Production web build** | Collector serves the built web UI itself — one process instead of `npm run dev` on the phone | S |
| 16 | **Multiple tags** | Track several trackers; pick tag in UI/app | M |
| 17 | **Adaptive polling** | Poll faster when the tag is moving, slower when still — fewer Google calls, better traces | S |
| 18 | **Tests + CI** | Unit tests for decoding/stops/geofences, GitHub Actions on PRs | M |

Effort: S = a few hours, M = about a day.

---

## Recommendation

Start with **1 → 2 → 3** (one small batch), then **5 + 6 + 7** together as
"end-of-day report". That covers reliability and the original goal.

Decisions needed from you:
1. Alert channel: **ntfy.sh** (no account, app on phone — recommended) or Telegram bot?
2. Daily report time, e.g. **23:30**?
3. Where the report goes: same channel as alerts (recommended), or email?
