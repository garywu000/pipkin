from io import BytesIO
import json
from pathlib import Path
import plistlib
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import server


class _FakeResponse:
    def __init__(self, payload):
        self._buf = BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class UpdateCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="pipkin-update-test-")
        self.addCleanup(self.tmp.cleanup)
        self.settings_file = Path(self.tmp.name) / "settings.json"
        p = patch.object(server, "SETTINGS_FILE", self.settings_file)
        p.start()
        self.addCleanup(p.stop)
        with server._UPDATE_LOCK:
            server._UPDATE_CACHE["checked_at"] = 0.0
            server._UPDATE_CACHE["ttl"] = 0.0
            server._UPDATE_CACHE["payload"] = None

    def test_version_constants_stay_in_sync(self):
        root = Path(__file__).resolve().parent.parent
        plist = plistlib.loads((root / "shell" / "Info.plist").read_bytes())
        csproj = ET.fromstring((root / "windows" / "PipkinWindows.csproj").read_text(encoding="utf-8"))
        win_ver = csproj.findtext(".//Version")
        self.assertEqual(server.APP_VERSION, plist["CFBundleShortVersionString"])
        self.assertEqual(server.APP_VERSION, win_ver)

    def test_parse_semver_and_comparison(self):
        self.assertEqual(server.parse_semver("v2.0.3"), (2, 0, 3))
        self.assertEqual(server.parse_semver("2.1"), (2, 1, 0))
        self.assertIsNone(server.parse_semver("v2.1.0-beta.1"))
        self.assertIsNone(server.parse_semver("not-a-version"))
        self.assertTrue(server.is_newer_version("v2.1.0", "2.0.3"))
        self.assertFalse(server.is_newer_version("v2.0.3", "2.0.3"))
        self.assertFalse(server.is_newer_version("v1.9.9", "2.0.3"))

    def test_disabled_preference_makes_zero_network_calls(self):
        server.update_settings_file(check_updates=False)
        opener = Mock()
        result = server.check_latest_release(opener=opener)
        self.assertFalse(result["enabled"])
        self.assertFalse(result["available"])
        opener.assert_not_called()

    def test_newer_release_detected_and_url_validated(self):
        opener = Mock(return_value=_FakeResponse({
            "tag_name": "v9.9.9",
            "html_url": "https://evil.example/phish",
            "draft": False,
            "prerelease": False,
        }))
        result = server.check_latest_release(opener=opener)
        self.assertTrue(result["available"])
        self.assertEqual(result["latest"], "9.9.9")
        # Untrusted html_url must be replaced with the official GitHub Releases URL
        self.assertEqual(result["url"], "https://github.com/garywu000/pipkin/releases/tag/v9.9.9")
        # Second call uses cache (no extra network call)
        cached = server.check_latest_release(opener=opener)
        self.assertEqual(cached["latest"], "9.9.9")
        self.assertEqual(opener.call_count, 1)

    def test_prerelease_and_network_error_fail_quietly(self):
        opener = Mock(return_value=_FakeResponse({
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/garywu000/pipkin/releases/tag/v9.9.9",
            "prerelease": True,
        }))
        result = server.check_latest_release(opener=opener)
        self.assertFalse(result["available"])
        self.assertTrue(result["checked"])

        with server._UPDATE_LOCK:
            server._UPDATE_CACHE["checked_at"] = 0.0
            server._UPDATE_CACHE["payload"] = None
        broken = Mock(side_effect=OSError("offline"))
        offline = server.check_latest_release(opener=broken)
        self.assertFalse(offline["available"])
        self.assertFalse(offline["checked"])

    def releases(self, *items):
        def release(tag, prerelease=False, draft=False):
            return {"tag_name": tag, "prerelease": prerelease, "draft": draft,
                    "html_url": f"https://github.com/garywu000/pipkin/releases/tag/{tag}", "assets": []}
        return Mock(return_value=_FakeResponse([release(*item) if isinstance(item, tuple) else release(item) for item in items]))

    def fresh(self):
        with server._UPDATE_LOCK:
            server._UPDATE_CACHE["checked_at"] = 0.0
            server._UPDATE_CACHE["payload"] = None

    def test_betas_are_offered_only_when_turned_on(self):
        listing = ("v2.3.0", ("v2.3.1", True), ("v2.4.0", False, True))   # official, beta, draft
        with patch.object(server, "APP_VERSION", "2.3.0"):
            result = server.check_latest_release(opener=self.releases(*listing))
            self.assertFalse(result["available"], "betas and drafts are ignored by default")
            self.assertFalse(result["beta"])
            server.update_settings_file(beta_updates=True)
            result = server.check_latest_release(opener=self.releases(*listing))   # cache is ignored: setting changed
            self.assertTrue(result["available"])
            self.assertEqual((result["latest"], result["channel"]), ("2.3.1", "beta"))
            self.assertFalse(result["required"])
            self.fresh()
            newer_official = server.check_latest_release(opener=self.releases("v2.4.0", ("v2.3.1", True)))
            self.assertEqual((newer_official["latest"], newer_official["channel"]), ("2.4.0", "stable"))
        with patch.object(server, "APP_VERSION", "2.3.1"):
            server.update_settings_file(beta_updates=False)
            self.fresh()
            on_beta = server.check_latest_release(opener=self.releases("v2.3.0", ("v2.3.1", True)))
            self.assertFalse(on_beta["available"], "turning betas off never offers an older version")

    def test_a_whole_major_version_behind_is_required(self):
        with patch.object(server, "APP_VERSION", "1.4.0"):
            result = server.check_latest_release(opener=self.releases("v3.0.0", "v2.9.0"))
            self.assertTrue(result["required"])
            self.assertEqual((result["latest"], result["requiredVersion"]), ("3.0.0", "3.0.0"))
        with patch.object(server, "APP_VERSION", "2.3.0"):
            self.fresh()
            minor = server.check_latest_release(opener=self.releases("v2.9.0"))
            self.assertTrue(minor["available"])
            self.assertFalse(minor["required"], "a newer minor version is offered, not forced")
            server.update_settings_file(beta_updates=True)
            self.fresh()
            beta_major = server.check_latest_release(opener=self.releases("v2.3.0", ("v3.0.0", True)))
            self.assertTrue(beta_major["available"])
            self.assertFalse(beta_major["required"], "a beta is never forced")

    def test_preference_endpoint_saves_the_beta_setting(self):
        body = json.dumps({"beta": True}).encode("utf-8")
        handler = SimpleNamespace(headers={"Content-Length": str(len(body))}, rfile=BytesIO(body), send_json=Mock())
        server.ChatHandler.set_update_preference(handler)
        status, reply = handler.send_json.call_args.args
        self.assertEqual((status, reply["beta"], reply["enabled"]), (200, True, True))
        self.assertTrue(server.beta_updates_enabled())
        for bad in ({}, {"beta": "yes"}):
            body = json.dumps(bad).encode("utf-8")
            handler = SimpleNamespace(headers={"Content-Length": str(len(body))}, rfile=BytesIO(body), send_json=Mock())
            server.ChatHandler.set_update_preference(handler)
            self.assertEqual(handler.send_json.call_args.args[0], 400)

    def test_preference_endpoint_persists_setting(self):
        body = json.dumps({"enabled": False}).encode("utf-8")
        handler = SimpleNamespace(
            headers={"Content-Length": str(len(body))},
            rfile=BytesIO(body),
            send_json=Mock(),
        )
        server.ChatHandler.set_update_preference(handler)
        self.assertEqual(handler.send_json.call_args.args[0], 200)
        self.assertFalse(server.update_check_enabled())

    def test_announces_next_step_catches_next_step_colon_heading(self):
        import agent
        reply = (
            "The average function adds 1, which is wrong. I will fix this issue and then run the tests.\n\n"
            "Next step: Fix the `average` function in `calc.py`."
        )
        self.assertTrue(agent.announces_next_step(reply))
        self.assertTrue(agent.announces_next_step("Next step: Run the test suite."))
        self.assertFalse(agent.announces_next_step("Fixed calc.py and ran the tests. Let me know if you want more changes."))

    def test_edit_file_recovers_double_escaped_newlines(self):
        import agent
        work_dir = Path(self.tmp.name) / "ws"
        work_dir.mkdir()
        (work_dir / "calc.py").write_text(
            'def grade(score):\n    if score > 90:\n        return "A"\n    return "B"\n',
            encoding="utf-8",
        )
        ws = agent.Workspace(work_dir)
        a = agent.Agent(None, lambda *_a, **_k: None, lambda: None, lambda *_a: True, workspace=ws, mode="auto", web=False)
        a.current_id = "t1"
        a.read_files = {agent.path_identity("calc.py")}
        result = a.execute("edit_file", {
            "path": "calc.py",
            "old_text": 'if score > 90:\\n        return "A"',
            "new_text": 'if score >= 90:\\n        return "A"',
        })
        self.assertIn("Updated calc.py", result)
        self.assertIn("if score >= 90:\n", (work_dir / "calc.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
