#!/usr/bin/env bash
# Builds the installable APK on your Mac. Fixes the JDK that `flutter build apk`
# needs (the cause of the Gradle/Java error), then builds and drops the APK
# next to this script as tag_trace.apk.
set -e
cd "$(dirname "$0")"

# 1. Make sure the Android project scaffold exists.
if [ ! -d android ]; then
  echo "Generating Android project..."
  bash setup.sh
fi

# 2. Pick a JDK that Gradle 9.1 is happy with (17-21).
JDK=""
for c in \
  "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
  "$(/usr/libexec/java_home -v 21 2>/dev/null)" \
  "$(/usr/libexec/java_home -v 17 2>/dev/null)"; do
  if [ -n "$c" ] && [ -x "$c/bin/java" ]; then JDK="$c"; break; fi
done
if [ -z "$JDK" ]; then
  echo "No JDK 17-21 found."
  echo "Install one of:  Android Studio   or:  brew install --cask temurin@21"
  exit 1
fi
echo "Using JDK: $JDK"
flutter config --jdk-dir "$JDK"

# 3. Build.
flutter pub get
flutter build apk --release

cp build/app/outputs/flutter-apk/app-release.apk ./tag_trace.apk
echo ""
echo "============================================================"
echo " APK ready:  $(pwd)/tag_trace.apk   ($(du -h tag_trace.apk | cut -f1))"
echo " Copy tag_trace.apk to your phone and tap it to install"
echo " (you'll be asked to allow installing from this source)."
echo "============================================================"
