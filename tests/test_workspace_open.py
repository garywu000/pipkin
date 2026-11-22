import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server


class WorkspaceOpenTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="pipkin-open-")
        self.addCleanup(directory.cleanup)
        self.parent = Path(directory.name)
        self.root = self.parent / "work"
        self.root.mkdir()
        self.workspace = server.Workspace(self.root)
        workspace_patch = patch.dict(server.WORKSPACES, clear=True)
        workspace_patch.start()
        self.addCleanup(workspace_patch.stop)
        self.key = server.grant_workspace(self.workspace)

    def open(self, path, *, root=None, key=None):
        payload = {"workspace": key or self.key, "root": root or str(self.workspace.path), "path": path}
        handler = SimpleNamespace(read_json=lambda _: payload, send_json=Mock())
        with patch.object(server.subprocess, "run") as launch:
            server.ChatHandler.open_workspace_file(handler)
        return handler.send_json.call_args.args[0], launch

    def test_html_uses_browser_and_executable_is_revealed(self):
        page = self.workspace.path / "index with spaces.html"
        page.write_text("<h1>Study guide</h1>")
        code = self.workspace.path / "test.command"
        code.write_text("#!/bin/sh\n")
        code.chmod(0o755)
        status, launch = self.open(str(page))
        self.assertEqual(status, 200)
        self.assertEqual(launch.call_args.args[0], ["/usr/bin/open", "-b", "com.apple.Safari", str(page)])
        status, launch = self.open("./test.command")
        self.assertEqual(status, 200)
        self.assertEqual(launch.call_args.args[0], ["/usr/bin/open", "-R", str(code)])

    def test_path_and_grant_are_checked_before_launch(self):
        outside = self.parent / "outside.html"
        outside.write_text("Outside")
        (self.root / ".env").write_text("Private")
        (self.root / "alias.html").symlink_to(outside)
        os.link(outside, self.root / "hard-link.html")
        for path in ("../outside.html", ".env", "alias.html", "hard-link.html", str(outside), "missing.html"):
            with self.subTest(path=path):
                status, launch = self.open(path)
                self.assertEqual(status, 404)
                launch.assert_not_called()
        for fields in ({"key": "a" * 32}, {"root": str(outside.parent)}):
            status, launch = self.open("index.html", **fields)
            self.assertEqual(status, 403)
            launch.assert_not_called()
