# Tag Trace

An Android app that logs your JioTag 2's locations to a per-day JSON file and
draws each day's trace (00:00 to now) on a map. Built with Flutter.

## Standalone (phone only)

To run everything on the phone with no separate machine, see **SETUP-TERMUX.md**.

## First-time setup (run once)

The Android native scaffold isn't checked in, so generate it and fetch packages:

```
cd ~/Documents/task3
bash setup.sh
```

`setup.sh` runs `flutter create` for the Android project, adds the network
permissions, and runs `flutter pub get`. Then:

```
flutter run          # on a connected Android phone
# or
flutter build apk    # build/app/outputs/flutter-apk/app-release.apk
```

## Why there are two parts

Google has no official API for Find Hub tags, and Find Hub keeps no location
history. The only proven way to read a tag's location is the open-source
[GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools), which signs
in to your Google account and decrypts the tag's reports.

So this is two pieces:

- **`collector/collector.py`** - runs on an always-on machine (your Mac or a
  Raspberry Pi), fetches the tag every 15 minutes via GoogleFindMyTools, keeps
  a deduplicated JSON log, and serves it over your home network.
- **The Flutter app** - reads that log, stores one JSON file per day on the
  phone, and shows the day list and the map trace.

If you'd rather not run a separate machine, the collector can also run on the
phone itself (Termux) and write a local JSON file the app reads directly.

## The app

```
lib/
  models/location_point.dart      one location report
  services/
    track_store.dart              per-day JSON files in app documents
    location_source.dart          read from HTTP collector or a local file
    settings.dart                 which source, and the last-synced mark
    sync_service.dart             pull new points, file them by day
    background.dart               15-min background sync (WorkManager)
  screens/
    day_list_screen.dart          home: days with a point, + Sync now
    day_map_screen.dart           tap a day -> trace 00:00..now on a map
    settings_screen.dart          set the collector address / file path
```

In the app: open **Settings**, point it at your collector (either the Wi-Fi
address like `http://192.168.1.50:8020`, or a local JSON file path), tap
**Test**, then **Save**. On the home screen, **Sync now** pulls the latest;
after that it also syncs every ~15 minutes on its own. Tap any day to see its
trace. The map uses OpenStreetMap tiles (no API key).

## The collector

1. Clone GoogleFindMyTools and install its requirements:
   ```
   git clone https://github.com/leonboe1/GoogleFindMyTools
   cd GoogleFindMyTools
   pip install -r requirements.txt
   ```
2. Copy `collector/collector.py` into that folder (or set `GFMT_DIR` to point
   at it).
3. First run signs you in to Google in a Chrome window (one time; it caches a
   token). In Find Hub, set the network to **"With network in all areas"** so
   the tag updates even in quiet places.
4. Run it:
   ```
   python3 collector.py --tag "JioTag" --interval 900 --port 8020 --out locations.json
   ```
   The app then reads `http://<this-machine-ip>:8020/locations`.

   For the phone-only (Termux) setup, add `--no-serve` and point the app's
   **Local file** setting at the `locations.json` path.

## What to expect

A tag only reports when an Android phone passes near it, so the trace is a
series of points, not a continuous GPS track, and quiet periods may have gaps.
The collector holds your Google login token, so keep the machine it runs on
private.
