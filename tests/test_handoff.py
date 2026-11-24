from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="pipkin-handoff-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings_file = self.root / "settings.json"
        self.handoff_dir = self.root / "iCloud-Pipkin"
        self.handoff_dir.mkdir()
        p = patch.object(server, "SETTINGS_FILE", self.settings_file)
        p.start()
        self.addCleanup(p.stop)

    def _make_handler(self, body=b""):
        out = BytesIO()
        handler = SimpleNamespace(
            headers={"Content-Length": str(len(body))},
            rfile=BytesIO(body),
            wfile=out,
            send_json=Mock(),
            headers_for=Mock(),
        )
        handler._handoff_dir = lambda: server.ChatHandler._handoff_dir(handler)
        handler._handoff_status_payload = lambda f: server.ChatHandler._handoff_status_payload(handler, f)
        return handler, out

    def test_status_save_load_and_clear_encrypted_and_plain(self):
        # Unconfigured initially
        h, _ = self._make_handler()
        server.ChatHandler.handoff_status(h)
        self.assertEqual(h.send_json.call_args.args[0], 200)
        self.assertFalse(h.send_json.call_args.args[1]["configured"])

        # Configure handoff_dir
        server.update_settings_file(handoff_dir=str(self.handoff_dir))
        h, _ = self._make_handler()
        server.ChatHandler.handoff_status(h)
        payload = h.send_json.call_args.args[1]
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["folderName"], "iCloud-Pipkin")
        self.assertFalse(payload["hasSnapshot"])

        # Save encrypted backup
        enc_blob = json.dumps({
            "format": "pipkin-backup",
            "version": 2,
            "encrypted": True,
            "kdf": "PBKDF2-SHA256",
            "iterations": 310000,
            "salt": "AAAA",
            "iv": "BBBB",
            "ciphertext": "CCCC",
        }).encode("utf-8")
        h, _ = self._make_handler(enc_blob)
        server.ChatHandler.save_handoff_snapshot(h)
        self.assertEqual(h.send_json.call_args.args[0], 200)
        saved = h.send_json.call_args.args[1]
        self.assertTrue(saved["hasSnapshot"])
        self.assertTrue(saved["encrypted"])

        # Load it back
        h, out = self._make_handler()
        server.ChatHandler.load_handoff_snapshot(h)
        h.headers_for.assert_called_once()
        self.assertEqual(out.getvalue(), enc_blob)

        # Reject invalid payload
        bad = json.dumps({"hello": "world"}).encode("utf-8")
        h, _ = self._make_handler(bad)
        server.ChatHandler.save_handoff_snapshot(h)
        self.assertEqual(h.send_json.call_args.args[0], 400)

        # Clear folder
        h, _ = self._make_handler()
        server.ChatHandler.clear_handoff_folder(h)
        self.assertFalse(h.send_json.call_args.args[1]["configured"])

    def test_symlink_handoff_file_is_rejected(self):
        server.update_settings_file(handoff_dir=str(self.handoff_dir))
        outside = self.root / "outside.json"
        outside.write_text('{"app":"pipkin","stores":{}}', encoding="utf-8")
        target = self.handoff_dir / server.HANDOFF_FILENAME
        try:
            target.symlink_to(outside)
        except OSError:
            self.skipTest("OS lacks symlink permission")
        h, _ = self._make_handler()
        server.ChatHandler.load_handoff_snapshot(h)
        self.assertEqual(h.send_json.call_args.args[0], 400)


if __name__ == "__main__":
    unittest.main()
