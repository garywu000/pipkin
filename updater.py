"""One-click updates: download a signed release, verify it, stage it, and hand a swap-and-relaunch helper to the shell.

Trust model: every release ships `pipkin-update.json`, a manifest listing each download's size and SHA-256, signed with
an Ed25519 key that only exists on the maintainer's Mac (never on GitHub). The app embeds the public key below and
installs nothing whose manifest signature, version, size, or hash does not check out, so a compromised GitHub account
or download host cannot push code through the updater.

Maintainer commands (macOS; the private key lives in the login Keychain):
    python3 updater.py keygen                    # once: create the key, print the public key to paste below
    python3 updater.py sign 2.0.5 <zip> [<zip>]  # each release: writes pipkin-update.json next to the first zip
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
from threading import Lock, Thread
import time
import urllib.request
import zipfile

UPDATE_PUBLIC_KEY = "bb0970ffb1ea639ba061710d61b8356632bd27b69fff66129ca2e7a5b81e730d"
MANIFEST_NAME = "pipkin-update.json"
MANIFEST_FORMAT = 1
BUNDLE_ID = "local.browser-model"
DOWNLOAD_PREFIX = "https://github.com/garywu000/pipkin/releases/download/"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ASSET_BYTES = 400 * 1024 * 1024
KEYCHAIN_SERVICE = "pipkin-release-signing"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# ---------------------------------------------------------------------------------------------------------------
# Ed25519 (RFC 8032), pure Python. Verification is all the app needs; signing is for the maintainer's release step.
# ---------------------------------------------------------------------------------------------------------------
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    d = 2 * z1 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(scalar, point):
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _same(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(point):
    zinv = pow(point[2], _P - 2, _P)
    x, y = point[0] * zinv % _P, point[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(data):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _hash_int(*parts):
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def _expand(secret):
    digest = hashlib.sha512(secret).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar = (scalar & ((1 << 254) - 8)) | (1 << 254)
    return scalar, digest[32:]


def ed25519_public_key(secret):
    return _compress(_mul(_expand(secret)[0], _G))


def ed25519_sign(secret, message):
    scalar, prefix = _expand(secret)
    public = _compress(_mul(scalar, _G))
    r = _hash_int(prefix, message) % _L
    big_r = _compress(_mul(r, _G))
    s = (r + _hash_int(big_r, public, message) * scalar) % _L
    return big_r + s.to_bytes(32, "little")


def ed25519_verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    a, r = _decompress(public), _decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _L:
        return False
    h = _hash_int(signature[:32], public, message) % _L
    return _same(_mul(s, _G), _add(r, _mul(h, a)))


# ---------------------------------------------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------------------------------------------
class UpdateError(Exception):
    """A user-facing reason the update cannot continue."""


def parse_version(value):
    match = VERSION_RE.match(str(value or "").strip().lstrip("v"))
    return tuple(int(part) for part in match.groups()) if match else None


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def make_manifest(version, files, secret):
    """files: {asset_key: Path}. Returns the signed manifest dict."""
    payload = {"format": MANIFEST_FORMAT, "app": "pipkin", "version": version, "assets": {}}
    for key, path in files.items():
        data = Path(path).read_bytes()
        payload["assets"][key] = {"name": Path(path).name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {"payload": payload, "signature": ed25519_sign(secret, canonical(payload)).hex()}


def verify_manifest(raw, public_hex=None):
    """Returns the verified payload, or raises UpdateError."""
    public_hex = public_hex or UPDATE_PUBLIC_KEY
    try:
        doc = json.loads(raw.decode("utf-8"))
        payload, signature = doc["payload"], bytes.fromhex(doc["signature"])
        public = bytes.fromhex(public_hex)
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as error:
        raise UpdateError("The update information is unreadable.") from error
    if not isinstance(payload, dict) or not ed25519_verify(public, canonical(payload), signature):
        raise UpdateError("The update's signature did not match. Pipkin did not install it.")
    if payload.get("format") != MANIFEST_FORMAT or payload.get("app") != "pipkin" or not parse_version(payload.get("version")):
        raise UpdateError("The update information is for a different app or format.")
    assets = payload.get("assets")
    if not isinstance(assets, dict):
        raise UpdateError("The update information lists no downloads.")
    for entry in assets.values():
        if (not isinstance(entry, dict) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", str(entry.get("name", "")))
                or not isinstance(entry.get("size"), int) or not 0 < entry["size"] <= MAX_ASSET_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))):
            raise UpdateError("The update information has an invalid download entry.")
    return payload


def asset_key(platform_name=None, machine=None):
    plat = platform_name or sys.platform
    if plat == "darwin":
        arch = machine or os.uname().machine
        return "macos-arm64" if arch == "arm64" else "macos-x86_64"
    if plat == "win32":
        return "windows-x64"
    return None


# ---------------------------------------------------------------------------------------------------------------
# Where Pipkin is installed, and whether it can replace itself
# ---------------------------------------------------------------------------------------------------------------
def install_root(here=None, platform_name=None):
    """The folder to replace (Pipkin.app or the Windows Pipkin folder), or None when running from source."""
    plat = platform_name or sys.platform
    here = Path(here or __file__).resolve().parent
    if plat == "darwin":
        bundle = here.parent.parent
        if here.name == "Resources" and here.parent.name == "Contents" and bundle.suffix == ".app":
            try:
                info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
            except (OSError, ValueError):
                return None
            return bundle if info.get("CFBundleIdentifier") == BUNDLE_ID else None
        return None
    if plat == "win32":
        return here if (here / "Pipkin.exe").is_file() and not (here / ".git").exists() else None
    return None


def install_blocker(root, platform_name=None):
    """A sentence explaining why one-click updates are unavailable, or None."""
    plat = platform_name or sys.platform
    if asset_key(plat) is None:
        return "One-click updates are available in the Mac and Windows apps."
    if root is None:
        return "This copy of Pipkin runs from source, so update it with git."
    if "/AppTranslocation/" in str(root):
        return "Move Pipkin to your Applications folder, then reopen it to enable one-click updates."
    if not (os.access(root, os.W_OK) and os.access(root.parent, os.W_OK)):
        return f"Pipkin can't write to {root.parent}, so download the new version from GitHub instead."
    return None


# ---------------------------------------------------------------------------------------------------------------
# Download, verify, extract, and stage
# ---------------------------------------------------------------------------------------------------------------
def _fetch(url, limit, opener, progress=None, sink=None):
    if not url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError("The download link is not on Pipkin's GitHub releases page.")
    request = urllib.request.Request(url, headers={"User-Agent": "Pipkin", "Accept": "application/octet-stream"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            final = getattr(response, "geturl", lambda: url)()
            if not str(final).startswith("https://"):
                raise UpdateError("The download was redirected to an insecure address.")
            received, chunks = 0, []
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > limit:
                    raise UpdateError("The download is larger than expected.")
                (sink.write(chunk) if sink else chunks.append(chunk))
                if progress:
                    progress(received)
            return received if sink else b"".join(chunks)
    except UpdateError:
        raise
    except OSError as error:
        raise UpdateError("The download stopped. Check your connection and try again.") from error


def _safe_names(archive, top):
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("__MACOSX/"):
            continue
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith("/") or ".." in parts or ":" in parts[0] or parts[0] != top
                or stat.S_ISLNK(info.external_attr >> 16)):
            raise UpdateError("The downloaded archive contains an unsafe path.")


def extract(zip_path, dest, top, platform_name=None):
    """Extracts a verified zip whose single top-level folder is `top` into dest; returns dest/top."""
    plat = platform_name or sys.platform
    with zipfile.ZipFile(zip_path) as archive:
        _safe_names(archive, top)
        if plat == "darwin":
            archive.close()
            # ditto keeps executable bits and the code signature intact, which zipfile does not.
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(dest)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            archive.extractall(dest)
    staged = Path(dest) / top
    if not staged.is_dir():
        raise UpdateError("The downloaded archive is missing Pipkin.")
    return staged


def check_staged(staged, version, platform_name=None):
    plat = platform_name or sys.platform
    if plat == "darwin":
        try:
            info = plistlib.loads((staged / "Contents" / "Info.plist").read_bytes())
        except (OSError, ValueError) as error:
            raise UpdateError("The downloaded app is incomplete.") from error
        if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleShortVersionString") != version:
            raise UpdateError("The downloaded app does not match the announced version.")
        if not (staged / "Contents" / "MacOS" / "Pipkin").is_file():
            raise UpdateError("The downloaded app is incomplete.")
        return
    server = staged / "server.py"
    if not (staged / "Pipkin.exe").is_file() or not server.is_file():
        raise UpdateError("The downloaded app is incomplete.")
    match = re.search(r'^APP_VERSION = "([^"]+)"', server.read_text(encoding="utf-8", errors="replace"), re.M)
    if not match or match.group(1) != version:
        raise UpdateError("The downloaded app does not match the announced version.")


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def helper_script(root, staged, backup, result, server_pid, version, platform_name=None):
    """The swap-and-relaunch script the shell runs after Pipkin quits. Rolls back if the swap fails."""
    plat = platform_name or sys.platform
    if plat == "win32":
        return f"""param([int]$AppPid = 0)
$ErrorActionPreference = 'Stop'
$app = {_ps_quote(root)}; $new = {_ps_quote(staged)}; $backup = {_ps_quote(backup)}; $result = {_ps_quote(result)}
$version = {_ps_quote(version)}
function Write-Result($ok, $message) {{
  @{{ ok = $ok; version = $version; message = $message }} | ConvertTo-Json | Set-Content -LiteralPath $result -Encoding UTF8
}}
foreach ($id in @($AppPid, {int(server_pid)})) {{
  if ($id -gt 0) {{ try {{ Wait-Process -Id $id -Timeout 60 -ErrorAction SilentlyContinue }} catch {{ }} }}
}}
Start-Sleep -Milliseconds 600
$ok = $false; $message = ''
try {{
  if (Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Recurse -Force }}
  $moved = $false
  for ($i = 0; $i -lt 20 -and -not $moved; $i++) {{
    try {{ Move-Item -LiteralPath $app -Destination $backup; $moved = $true }} catch {{ Start-Sleep -Milliseconds 500 }}
  }}
  if (-not $moved) {{ throw 'Pipkin was still running, so its files could not be replaced.' }}
  try {{
    try {{ Move-Item -LiteralPath $new -Destination $app }} catch {{ Copy-Item -LiteralPath $new -Destination $app -Recurse }}
    if (-not (Test-Path -LiteralPath (Join-Path $app 'Pipkin.exe'))) {{ throw 'The new version is incomplete.' }}
    $ok = $true
  }} catch {{
    $message = $_.Exception.Message
    if (Test-Path -LiteralPath $app) {{ Remove-Item -LiteralPath $app -Recurse -Force }}
    Move-Item -LiteralPath $backup -Destination $app
  }}
}} catch {{ $message = $_.Exception.Message }}
if ($ok) {{ Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue }}
Write-Result $ok $message
if ($env:PIPKIN_UPDATE_NO_RELAUNCH -ne '1') {{ Start-Process -FilePath (Join-Path $app 'Pipkin.exe') }}
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""
    q = shlex.quote
    return f"""#!/bin/sh
# Pipkin update helper: waits for Pipkin to quit, swaps in the verified new version, and reopens it.
trap '' HUP
APP={q(str(root))}; NEW={q(str(staged))}; BACKUP={q(str(backup))}; RESULT={q(str(result))}; VERSION={q(version)}
wait_for() {{
  [ "${{1:-0}}" -gt 0 ] 2>/dev/null || return 0
  i=0; while kill -0 "$1" 2>/dev/null && [ $i -lt 200 ]; do sleep 0.3; i=$((i + 1)); done
}}
wait_for "${{1:-0}}"
wait_for {int(server_pid)}
ok=false; message=""
rm -rf "$BACKUP"
if mv "$APP" "$BACKUP"; then
  if mv "$NEW" "$APP" && [ -x "$APP/Contents/MacOS/Pipkin" ]; then
    ok=true
  else
    message="The new version could not be put in place, so Pipkin kept the current one."
    rm -rf "$APP"; mv "$BACKUP" "$APP"
  fi
else
  message="Pipkin could not move its current version aside."
fi
printf '{{"ok": %s, "version": "%s", "message": "%s", "at": %s}}\\n' "$ok" "$VERSION" "$message" "$(date +%s)" > "$RESULT"
[ "$PIPKIN_UPDATE_NO_RELAUNCH" = "1" ] || /usr/bin/open "$APP"
rm -f "$0"
"""


class Updater:
    """Background download-and-stage job. One per server process."""

    def __init__(self, data_dir, root=None, public_hex=None, opener=None, platform_name=None):
        self.dir = Path(data_dir) / "updates"
        self.root = root
        self.public_hex = public_hex
        self.opener = opener
        self.platform = platform_name or sys.platform
        self.server_pid = os.getpid()
        self.lock = Lock()
        self.state = {"state": "idle"}
        self.result_read = False

    def _read_result(self):
        path = self.dir / "result.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            path.unlink()
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return {"ok": data.get("ok") is True, "version": str(data.get("version", ""))[:20],
                "message": str(data.get("message", ""))[:300]}

    def status(self):
        with self.lock:
            status = dict(self.state)
        if not self.result_read:  # reported once, on the first status request after a relaunch
            self.result_read = True
            result = self._read_result()
            if result:
                status["lastResult"] = result
        return status

    def _set(self, **state):
        with self.lock:
            self.state = state

    def start(self, release, current_version):
        """release: {"version": "2.0.5", "assets": {name: url}}. Returns the new status."""
        blocker = install_blocker(self.root, self.platform)
        if blocker:
            raise UpdateError(blocker)
        if not (parse_version(release.get("version")) and parse_version(release["version"]) > parse_version(current_version)):
            raise UpdateError("There is no newer version to install.")
        with self.lock:
            if self.state["state"] in ("downloading", "verifying"):
                return dict(self.state)
            if self.state["state"] == "ready" and self.state.get("version") == release["version"]:
                return dict(self.state)
            self.state = {"state": "downloading", "version": release["version"], "received": 0, "total": 0}
        Thread(target=self._run, args=(release, current_version), daemon=True).start()
        return self.status()

    def _run(self, release, current_version):
        version = release["version"]
        try:
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            urls = release.get("assets") or {}
            if MANIFEST_NAME not in urls:
                raise UpdateError("This release does not support one-click updates yet.")
            payload = verify_manifest(_fetch(urls[MANIFEST_NAME], MAX_MANIFEST_BYTES, self.opener), self.public_hex)
            if payload["version"] != version or parse_version(version) <= parse_version(current_version):
                raise UpdateError("The update information does not match the newest release.")
            entry = payload["assets"].get(asset_key(self.platform))
            if not entry or entry["name"] not in urls:
                raise UpdateError("This release has no download for this computer.")
            self._set(state="downloading", version=version, received=0, total=entry["size"])
            for old in self.dir.glob("staged-*"):
                shutil.rmtree(old, ignore_errors=True)
            part = self.dir / "download.part"
            digest = hashlib.sha256()

            class Sink:
                def __init__(self, handle):
                    self.handle = handle

                def write(self, chunk):
                    digest.update(chunk)
                    self.handle.write(chunk)

            with open(part, "wb") as handle:
                size = _fetch(urls[entry["name"]], entry["size"], self.opener, sink=Sink(handle),
                              progress=lambda n: self._set(state="downloading", version=version, received=n, total=entry["size"]))
            self._set(state="verifying", version=version)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                part.unlink(missing_ok=True)
                raise UpdateError("The download did not match the signed release. Pipkin did not install it.")
            staging = self.dir / f"staged-{version}"
            staged = extract(part, staging, "Pipkin" if self.platform == "win32" else "Pipkin.app", self.platform)
            part.unlink(missing_ok=True)
            check_staged(staged, version, self.platform)
            # Windows renames the old folder beside itself (same drive, so it is instant); the Mac keeps the previous
            # app in Pipkin's data folder so a second Pipkin never shows up in Applications.
            backup = (self.root.parent / (self.root.name + ".previous")) if self.platform == "win32" else self.dir / "previous-version.app"
            script = self.dir / ("apply-update.ps1" if self.platform == "win32" else "apply-update.sh")
            script.write_text(helper_script(self.root, staged, backup, self.dir / "result.json", self.server_pid, version,
                                            self.platform), encoding="utf-8")
            if self.platform != "win32":
                script.chmod(0o700)
            self._set(state="ready", version=version)
        except UpdateError as error:
            self._set(state="error", version=version, error=str(error))
        except (OSError, zipfile.BadZipFile, subprocess.CalledProcessError):
            self._set(state="error", version=version, error="The update could not be prepared. Try again, or download it from GitHub.")


# ---------------------------------------------------------------------------------------------------------------
# Maintainer CLI
# ---------------------------------------------------------------------------------------------------------------
def _keychain_secret():
    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             check=True, capture_output=True, text=True).stdout.strip()
        return bytes.fromhex(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        sys.exit("No Pipkin release key in the Keychain. Run: python3 updater.py keygen")


def _main(argv):
    if argv[:1] == ["keygen"]:
        if subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
                          capture_output=True).returncode == 0:
            sys.exit("A Pipkin release key already exists in the Keychain; refusing to replace it.")
        secret = os.urandom(32)
        subprocess.run(["/usr/bin/security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "pipkin",
                        "-l", "Pipkin release signing key", "-w", secret.hex()], check=True)
        print(ed25519_public_key(secret).hex())
        return
    if argv[:1] == ["sign"] and len(argv) >= 3:
        version, zips = argv[1], [Path(p) for p in argv[2:]]
        files = {}
        for path in zips:
            key = "windows-x64" if "windows" in path.name else "macos-x86_64" if "x86_64" in path.name else "macos-arm64"
            files[key] = path
        secret = _keychain_secret()
        if ed25519_public_key(secret).hex() != UPDATE_PUBLIC_KEY:
            sys.exit("The Keychain key does not match UPDATE_PUBLIC_KEY in updater.py.")
        manifest = make_manifest(version, files, secret)
        verify_manifest(json.dumps(manifest).encode())
        out = zips[0].parent / MANIFEST_NAME
        out.write_text(json.dumps(manifest, indent=1) + "\n")
        print(f"Wrote {out}")
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    _main(sys.argv[1:])
