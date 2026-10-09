#!/usr/bin/env bash
# One-time setup: generates the Android native scaffold around the app source
# and fetches packages. Run once, then `flutter run` or `flutter build apk`.
set -e
APP="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
echo "Generating Android scaffold..."
flutter create --project-name tag_trace --org com.santosh --platforms android "$TMP/tag_trace"
# Copy the generated native scaffold in, without touching our Dart or config.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude lib --exclude test --exclude pubspec.yaml --exclude README.md \
    --exclude pubspec.lock "$TMP/tag_trace/" "$APP/"
else
  (cd "$TMP/tag_trace" && for d in android .metadata analysis_options.yaml .gitignore; do
    [ -e "$d" ] && cp -R "$d" "$APP/"; done)
fi
# Ensure the app can use the network and read local files.
python3 - "$APP/android/app/src/main/AndroidManifest.xml" <<'PY'
import sys, re
p = sys.argv[1]
s = open(p).read()
if 'android.permission.INTERNET' not in s:
    s = s.replace(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">',
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <uses-permission android:name="android.permission.INTERNET"/>\n'
        '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>\n'
        '    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE"/>',
        1)
if 'usesCleartextTraffic' not in s:
    s = re.sub(r'(android:label="[^"]*")',
               r'\1\n        android:usesCleartextTraffic="true"', s, count=1)
open(p, 'w').write(s)
print('AndroidManifest patched for network access')
PY
echo "Fetching packages..."
flutter pub get
echo "Done. Now:  flutter run   (device connected)   or   flutter build apk"
