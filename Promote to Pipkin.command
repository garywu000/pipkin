#!/bin/sh
# Copies the dev copy into Pipkin.app after syntax checks, keeping a backup of the current app.
# Pipkin's Develop › Promote menu runs this with --yes and PIPKIN_APP set.
set -eu
dev="$(cd "$(dirname "$0")" && pwd)"
outputs="$(dirname "$dev")"
bundle="${PIPKIN_APP:-$outputs/Pipkin.app}"
app="$bundle/Contents/Resources"
yes=0
[ "${1:-}" = "--yes" ] && yes=1
source="$dev"
if [ -n "${PIPKIN_PROMOTE_COMMIT:-}" ]; then
  source="$(mktemp -d /private/tmp/pipkin-promotion.XXXXXX)"
  trap 'rm -rf "$source"' EXIT
  git -C "$dev" archive --output="$source/release.tar" "$PIPKIN_PROMOTE_COMMIT"
  /usr/bin/tar -xf "$source/release.tar" -C "$source"
fi
cd "$source"
echo "Checking the dev copy…"
/usr/bin/python3 -c "import ast,sys;[ast.parse(open(f).read(),f) for f in sys.argv[1:]]" server.py agent.py artifacts.py platform_os.py skills.py scheduler.py updater.py github_connector.py github_workflows.py
if command -v node >/dev/null 2>&1; then
  for f in dist/*.js; do node --check "$f"; done
fi
if [ "$source" = "$dev" ] && [ -n "$(git status --porcelain)" ]; then
  echo "Note: there are uncommitted changes in pipkin-dev. They will be promoted too."
fi
if [ "$yes" != 1 ]; then
  printf "Copy pipkin-dev into Pipkin.app? [y/N] "
  read -r answer
  [ "$answer" = "y" ] || [ "$answer" = "Y" ] || { echo "Cancelled."; exit 1; }
fi
stamp="$(date +%Y%m%d-%H%M%S)"
parent="$(dirname "$bundle")"
mkdir -p "$outputs/backups"
(cd "$parent" && zip -qr "$outputs/backups/Pipkin-before-$stamp.zip" "$(basename "$bundle")")
rsync -a server.py agent.py artifacts.py platform_os.py skills.py scheduler.py updater.py github_connector.py github_workflows.py read-pdf read-pdf.m "$app/"
rsync -a --delete dist/ "$app/dist/"
rsync -a shell/ "$app/shell/"
cp shell/Info.plist "$bundle/Contents/Info.plist"
shell_signature="$(/usr/bin/shasum shell/build.sh shell/PipkinApp.m shell/PipkinBrowser.h shell/PipkinBrowser.m shell/PipkinPet.h shell/PipkinPet.m)"
if [ "$shell_signature" != "$(cat "$outputs/backups/.last-shell.sha" 2>/dev/null || true)" ]; then
  sh shell/build.sh "$bundle"
  printf '%s\n' "$shell_signature" > "$outputs/backups/.last-shell.sha"
  echo "Rebuilt the native app. Quit and reopen Pipkin to use it."
fi
/usr/bin/codesign --force --sign - "$bundle"
/usr/bin/codesign --verify --strict "$bundle"
(cd "$parent" && rm -f Pipkin.zip && zip -qr Pipkin.zip "$(basename "$bundle")")
git -C "$dev" tag "promoted-$stamp" "${PIPKIN_PROMOTE_COMMIT:-HEAD}" >/dev/null 2>&1 || true
echo "Done. Backup: backups/Pipkin-before-$stamp.zip"
echo "Reload Pipkin tabs for page changes. For server.py/agent.py changes, close every Pipkin tab, wait a few seconds, then open Pipkin.app again."
