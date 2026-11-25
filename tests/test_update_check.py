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
