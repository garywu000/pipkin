import json
import os
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import server  # noqa: E402


def fake_answers(*addresses):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)) for address in addresses]


class PinnedConnectionTests(unittest.TestCase):
    def test_private_answer_is_refused_before_connecting(self):
        with mock.patch.object(agent.socket, "getaddrinfo", return_value=fake_answers("127.0.0.1")), \
                mock.patch.object(agent.socket, "socket") as make_socket:
            with self.assertRaises(ValueError):
                agent._pinned_connect(("rebind.example.com", 80), 5)
            make_socket.assert_not_called()

    def test_mixed_answers_are_refused(self):
        with mock.patch.object(agent.socket, "getaddrinfo", return_value=fake_answers("93.184.216.34", "10.0.0.1")):
            with self.assertRaises(ValueError):
                agent.public_addresses("mixed.example.com", 80)

    def test_connects_to_the_validated_address(self):
        fake = mock.MagicMock()
        with mock.patch.object(agent.socket, "getaddrinfo", return_value=fake_answers("93.184.216.34")), \
                mock.patch.object(agent.socket, "socket", return_value=fake):
            self.assertIs(agent._pinned_connect(("example.com", 80), 5), fake)
        fake.connect.assert_called_once_with(("93.184.216.34", 80))

    def test_connections_use_the_pinned_resolver(self):
        self.assertIs(agent._PinnedHTTPConnection("example.com")._create_connection, agent._pinned_connect)
        self.assertIs(agent._PinnedHTTPSConnection("example.com")._create_connection, agent._pinned_connect)


class SeatbeltProfileTests(unittest.TestCase):
    def test_profile_blocks_launching_and_scripting_apps(self):
        profile = agent.seatbelt_profile(Path(tempfile.gettempdir()), Path(tempfile.gettempdir()))
        self.assertIn("(deny appleevent-send)", profile)
        self.assertIn("com.apple.coreservices.launchservicesd", profile)
        self.assertIn('(literal "/usr/bin/open")', profile)
        self.assertIn("(deny network-outbound)", profile)


@unittest.skipIf(sys.platform == "win32", "POSIX hard links")
class WriteHardLinkTests(unittest.TestCase):
    def test_write_never_goes_through_a_hard_link(self):
        # Sync clients (Google Drive) hard-link files, so the write succeeds, but it replaces the workspace entry
        # instead of writing into the shared file: the outside link keeps its own content.
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "project"
            folder.mkdir()
            outside = Path(root) / "outside.txt"
            outside.write_text("keep")
            os.link(outside, folder / "linked.txt")
            workspace = agent.Workspace(folder)
            workspace.write_text("linked.txt", "overwritten")
            self.assertEqual(outside.read_text(), "keep")
            self.assertEqual((folder / "linked.txt").read_text(), "overwritten")

    def test_write_replaces_regular_file(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "project"
            folder.mkdir()
            (folder / "notes.txt").write_text("a much longer original text")
            workspace = agent.Workspace(folder)
            workspace.write_text("notes.txt", "short")
            self.assertEqual((folder / "notes.txt").read_text(), "short")


class PrivateFileTests(unittest.TestCase):
    def test_private_file_is_atomic_and_owner_only(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "nested" / "settings.json"
            server.write_private_file(target, json.dumps({"a": 1}).encode())
            server.write_private_file(target, json.dumps({"a": 2}).encode())
            self.assertEqual(json.loads(target.read_text()), {"a": 2})
            self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["settings.json"])
            if sys.platform != "win32":
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
