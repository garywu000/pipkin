"""One-click updates: signatures, manifests, safe extraction, install detection, and a full swap with the real helper."""
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
import zipfile

import updater

SECRET = bytes(range(32))
PUBLIC = updater.ed25519_public_key(SECRET).hex()
PREFIX = updater.DOWNLOAD_PREFIX + "v9.9.9/"

# RFC 8032 section 7.1, tests 1-3: (secret, public, message, signature)
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


class FakeResponse(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_for(files):
    def opener(request, timeout=None):
        if request.full_url not in files:
            raise OSError("not found")
        return FakeResponse(files[request.full_url], "https://objects.example/" + request.full_url.rsplit("/", 1)[-1])
    return opener


def mac_app(folder, version, marker="old"):
    app = Path(folder) / "Pipkin.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
        {"CFBundleIdentifier": updater.BUNDLE_ID, "CFBundleShortVersionString": version}))
    binary = app / "Contents" / "MacOS" / "Pipkin"
    binary.write_text("#!/bin/sh\necho " + marker + "\n")
    binary.chmod(0o755)
    (app / "Contents" / "Resources" / "server.py").write_text(f'APP_VERSION = "{version}"\n')
    return app


def win_app(folder, version, marker="old"):
    app = Path(folder) / "Pipkin"
    app.mkdir(parents=True)
    (app / "Pipkin.exe").write_text(marker)
    (app / "server.py").write_text(f'APP_VERSION = "{version}"\n')
    return app


def zip_folder(folder, top):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(Path(folder).rglob("*")):
            name = (Path(top) / path.relative_to(folder)).as_posix()
            if path.is_dir():
                archive.writestr(zipfile.ZipInfo(name + "/"), b"")
            else:
                info = zipfile.ZipInfo(name)
                info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
                archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


class Ed25519Test(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for secret, public, message, signature in VECTORS:
            secret, message = bytes.fromhex(secret), bytes.fromhex(message)
            self.assertEqual(updater.ed25519_public_key(secret).hex(), public)
            self.assertEqual(updater.ed25519_sign(secret, message).hex(), signature)
            self.assertTrue(updater.ed25519_verify(bytes.fromhex(public), message, bytes.fromhex(signature)))

    def test_rejects_tampering(self):
        signature = updater.ed25519_sign(SECRET, b"pipkin")
        public = bytes.fromhex(PUBLIC)
        self.assertFalse(updater.ed25519_verify(public, b"pipkiN", signature))
        self.assertFalse(updater.ed25519_verify(public, b"pipkin", signature[:-1] + bytes([signature[-1] ^ 1])))
        bad_s = signature[:32] + (updater._L + 1).to_bytes(32, "little")
        self.assertFalse(updater.ed25519_verify(public, b"pipkin", bad_s))
        self.assertFalse(updater.ed25519_verify(public[:31], b"pipkin", signature))

    def test_embedded_key_is_a_real_point(self):
        self.assertIsNotNone(updater._decompress(bytes.fromhex(updater.UPDATE_PUBLIC_KEY)))


class ManifestTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.zip = Path(directory.name) / "Pipkin-macos-arm64.zip"
        self.zip.write_bytes(b"zip bytes")

    def test_round_trip(self):
        manifest = updater.make_manifest("9.9.9", {"macos-arm64": self.zip}, SECRET)
        payload = updater.verify_manifest(json.dumps(manifest).encode(), PUBLIC)
        entry = payload["assets"]["macos-arm64"]
        self.assertEqual(entry, {"name": self.zip.name, "size": 9, "sha256": hashlib.sha256(b"zip bytes").hexdigest()})

    def test_tampered_or_foreign_manifests_fail(self):
        manifest = updater.make_manifest("9.9.9", {"macos-arm64": self.zip}, SECRET)
        tampered = json.loads(json.dumps(manifest))
        tampered["payload"]["assets"]["macos-arm64"]["sha256"] = "0" * 64
        for raw, key in [(json.dumps(tampered).encode(), PUBLIC), (json.dumps(manifest).encode(), updater.ed25519_public_key(b"\x01" * 32).hex()),
                         (b"not json", PUBLIC), (b'{"payload": {}, "signature": "zz"}', PUBLIC)]:
            with self.assertRaises(updater.UpdateError):
                updater.verify_manifest(raw, key)

    def test_signed_but_invalid_entries_fail(self):
        payload = {"format": 1, "app": "pipkin", "version": "9.9.9",
                   "assets": {"macos-arm64": {"name": "../evil", "size": 1, "sha256": "0" * 64}}}
        doc = {"payload": payload, "signature": updater.ed25519_sign(SECRET, updater.canonical(payload)).hex()}
        with self.assertRaises(updater.UpdateError):
            updater.verify_manifest(json.dumps(doc).encode(), PUBLIC)


class InstallDetectionTest(unittest.TestCase):
    def test_mac_bundle_and_source_checkouts(self):
        with TemporaryDirectory() as directory:
            app = mac_app(directory, "1.0.0")
            self.assertEqual(updater.install_root(app / "Contents/Resources/server.py", "darwin"), app.resolve())
            self.assertIsNone(updater.install_root(Path(directory) / "server.py", "darwin"))
            (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "someone.else"}))
            self.assertIsNone(updater.install_root(app / "Contents/Resources/server.py", "darwin"))

    def test_windows_folder(self):
        with TemporaryDirectory() as directory:
            app = win_app(directory, "1.0.0")
            self.assertEqual(updater.install_root(app / "server.py", "win32"), app.resolve())
            (app / ".git").mkdir()
            self.assertIsNone(updater.install_root(app / "server.py", "win32"))

    def test_blockers(self):
        self.assertIn("source", updater.install_blocker(None, "darwin"))
        self.assertIn("Mac and Windows", updater.install_blocker(Path("/x"), "linux"))
        self.assertIn("Applications", updater.install_blocker(Path("/private/var/folders/x/AppTranslocation/y/Pipkin.app"), "darwin"))
        with TemporaryDirectory() as directory:
            app = mac_app(directory, "1.0.0")
            self.assertIsNone(updater.install_blocker(app, "darwin"))


class SafeExtractTest(unittest.TestCase):
    def check(self, names, top="Pipkin.app", link=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                info = zipfile.ZipInfo(name)
                if name == link:
                    info.external_attr = (0o120777) << 16
                archive.writestr(info, b"x")
        with zipfile.ZipFile(buffer) as archive:
            updater._safe_names(archive, top)

    def test_rejects_escapes_links_and_strays(self):
        for names, link in [(["Pipkin.app/../evil"], None), (["/etc/passwd"], None), (["Other/x"], None),
                            (["Pipkin.app/a"], "Pipkin.app/a"), (["Pipkin.app/C:/x", "C:/x"], None)]:
            with self.assertRaises(updater.UpdateError, msg=names):
                self.check(names, link=link)
        self.check(["Pipkin.app/Contents/Info.plist", "__MACOSX/._x"])


@unittest.skipUnless(sys.platform in ("darwin", "win32"), "the swap helper runs on macOS and Windows")
class FullUpdateTest(unittest.TestCase):
    """Download → verify → stage → run the real helper script → the installed app is the new version."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.mac = sys.platform == "darwin"
        (self.base / "install").mkdir()
        (self.base / "build").mkdir()
        make = mac_app if self.mac else win_app
        self.root = make(self.base / "install", "1.0.0", "old").resolve()
        new = make(self.base / "build", "9.9.9", "new")
        self.top = new.name
        asset = "Pipkin-macos-arm64.zip" if self.mac else "Pipkin-windows-x64.zip"
        data = zip_folder(new, self.top)
        (self.base / asset).write_bytes(data)
        manifest = updater.make_manifest("9.9.9", {updater.asset_key(): self.base / asset}, SECRET)
        self.files = {PREFIX + asset: data, PREFIX + updater.MANIFEST_NAME: json.dumps(manifest).encode()}
        self.release = {"version": "9.9.9", "assets": {name.rsplit("/", 1)[-1]: name for name in self.files}}

    def updater(self, files=None):
        job = updater.Updater(self.base / "data", root=self.root, public_hex=PUBLIC, opener=opener_for(files or self.files))
        job.server_pid = 0
        return job

    def wait(self, job):
        deadline = time.monotonic() + 30
        while job.status()["state"] in ("downloading", "verifying") and time.monotonic() < deadline:
            time.sleep(0.05)
        return job.status()

    def test_installs_and_reports(self):
        job = self.updater()
        job.start(self.release, "1.0.0")
        status = self.wait(job)
        self.assertEqual(status["state"], "ready", status)
        script = job.dir / ("apply-update.sh" if self.mac else "apply-update.ps1")
        env = {**os.environ, "PIPKIN_UPDATE_NO_RELAUNCH": "1"}
        if self.mac:
            subprocess.run(["/bin/sh", str(script), "0"], check=True, env=env, timeout=60)
            installed = plistlib.loads((self.root / "Contents" / "Info.plist").read_bytes())["CFBundleShortVersionString"]
            self.assertEqual(installed, "9.9.9")
            self.assertTrue(os.access(self.root / "Contents" / "MacOS" / "Pipkin", os.X_OK))
            self.assertTrue((job.dir / "previous-version.app" / "Contents" / "Info.plist").exists())
        else:
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                            "-AppPid", "0"], check=True, env=env, timeout=120)
            self.assertEqual((self.root / "Pipkin.exe").read_text(), "new")
            self.assertFalse((self.root.parent / "Pipkin.previous").exists())
        self.assertFalse(script.exists())
        self.assertFalse(list(job.dir.glob("staged-*")), "the emptied staging folder should be cleaned up")
        result = self.updater().status()["lastResult"]
        self.assertEqual(result, {"ok": True, "version": "9.9.9", "message": ""})

    def test_corrupted_download_is_refused(self):
        files = dict(self.files)
        asset = next(name for name in files if name.endswith(".zip"))
        files[asset] = files[asset][:-10] + b"0123456789"
        job = self.updater(files)
        job.start(self.release, "1.0.0")
        status = self.wait(job)
        self.assertEqual(status["state"], "error")
        self.assertIn("did not match", status["error"])
        self.assertFalse(list(job.dir.glob("apply-update.*")))

    def test_wrong_signing_key_is_refused(self):
        job = self.updater()
        job.public_hex = updater.ed25519_public_key(b"\x02" * 32).hex()
        job.start(self.release, "1.0.0")
        self.assertIn("signature", self.wait(job)["error"])

    def test_old_or_unsigned_releases_are_refused(self):
        with self.assertRaises(updater.UpdateError):
            self.updater().start(self.release, "9.9.9")
        job = self.updater()
        job.start({"version": "9.9.9", "assets": {k: v for k, v in self.release["assets"].items() if not k.endswith(".json")}}, "1.0.0")
        self.assertIn("one-click", self.wait(job)["error"])


class ServerReleaseTest(unittest.TestCase):
    def test_release_assets_are_cached_and_filtered(self):
        import server
        release = {"tag_name": "v99.0.0", "html_url": server.RELEASE_URL_PREFIX + "tag/v99.0.0", "assets": [
            {"name": updater.MANIFEST_NAME, "browser_download_url": updater.DOWNLOAD_PREFIX + "v99.0.0/" + updater.MANIFEST_NAME},
            {"name": "evil.zip", "browser_download_url": "https://evil.example/evil.zip"}]}
        opener = lambda request, timeout=None: FakeResponse(json.dumps(release).encode(), request.full_url)
        with TemporaryDirectory() as directory:
            original = server.SETTINGS_FILE
            server.SETTINGS_FILE = Path(directory) / "settings.json"
            server._UPDATE_CACHE.update(checked_at=0.0, payload=None, release=None)
            try:
                payload = server.check_latest_release(force=True, opener=opener)
            finally:
                server.SETTINGS_FILE = original
        self.assertTrue(payload["available"])
        self.assertIn("installable", payload)
        self.assertEqual(list(server._UPDATE_CACHE["release"]["assets"]), [updater.MANIFEST_NAME])


if __name__ == "__main__":
    unittest.main()
