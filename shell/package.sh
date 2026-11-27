#!/bin/sh
# Build a fresh app and distribution zip from this source checkout.
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Pipkin's app bundle builds on macOS." >&2
  exit 1
fi
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
output="${1:-$repo/build}"
mkdir -p "$output"
output="$(cd "$output" && pwd -P)"
archive="Pipkin-macos-$(uname -m).zip"
if [ -e "$output/Pipkin.app" ] || [ -e "$output/$archive" ]; then
  echo "Choose an output folder without an existing Pipkin app or zip." >&2
  exit 1
fi

scratch="$(mktemp -d "$output/.pipkin.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
app="$scratch/Pipkin.app"
resources="$app/Contents/Resources"
picker="$resources/Folder Picker.app"
mkdir -p "$resources/shell" "$picker/Contents/MacOS"
cp "$here/Info.plist" "$app/Contents/Info.plist"
cp "$here/LocalModel.icns" "$resources/"
cp -R "$repo/dist" "$resources/"
cp "$repo/server.py" "$repo/agent.py" "$repo/artifacts.py" "$repo/platform_os.py" "$repo/skills.py" "$repo/scheduler.py" "$repo/updater.py" "$repo/github_connector.py" "$repo/github_workflows.py" \
  "$repo/choose-folder.m" "$repo/read-pdf.m" "$resources/"
cp "$here"/Pipkin*.m "$here"/Pipkin*.h "$here/Sprig.png" "$here/build.sh" "$resources/shell/"
cp "$repo/Folder Picker.app/Contents/Info.plist" "$picker/Contents/Info.plist"

xcrun clang -fobjc-arc -O2 -mmacosx-version-min=12.0 -framework AppKit \
  "$repo/choose-folder.m" -o "$picker/Contents/MacOS/choose-folder"
xcrun clang -fobjc-arc -O2 -mmacosx-version-min=12.0 -framework AppKit -framework PDFKit -framework CoreText -framework CoreGraphics \
  "$repo/read-pdf.m" -o "$resources/read-pdf"
sh "$here/build.sh" "$app"
/usr/bin/codesign --force --sign - "$picker"
/usr/bin/codesign --force --sign - "$resources/read-pdf"
/usr/bin/codesign --force --sign - "$app"
/usr/bin/codesign --verify --deep --strict "$app"
/usr/bin/ditto -c -k --norsrc --keepParent "$app" "$scratch/$archive"

mv "$app" "$scratch/$archive" "$output/"
echo "Built $output/Pipkin.app and $output/$archive"
