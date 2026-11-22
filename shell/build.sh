#!/bin/sh
# Builds Pipkin's native window (shell/PipkinApp.m) into Pipkin.app/Contents/MacOS/Pipkin.
# Usage: shell/build.sh [path/to/Pipkin.app]
# Works from inside the app bundle (Contents/Resources/shell) or from pipkin-dev/shell.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
app="${1:-${PIPKIN_APP:-}}"
if [ -z "$app" ]; then
  if [ -f "$here/../../Info.plist" ]; then
    app="$(cd "$here/../../.." && pwd)"
  else
    app="$(cd "$here/../.." && pwd)/Pipkin.app"
  fi
fi
[ -f "$app/Contents/Info.plist" ] || { echo "Pipkin.app not found at $app" >&2; exit 1; }
mkdir -p "$app/Contents/MacOS"
xcrun clang -fobjc-arc -O2 -Wall -mmacosx-version-min=12.0 \
  -framework Cocoa -framework WebKit -framework UserNotifications -framework QuartzCore \
  -o "$app/Contents/MacOS/Pipkin" "$here/PipkinApp.m" "$here/PipkinPet.m" "$here/PipkinBrowser.m"
codesign --force --sign - "$app/Contents/MacOS/Pipkin" >/dev/null 2>&1 || true
echo "Built $app/Contents/MacOS/Pipkin"
