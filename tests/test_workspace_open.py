import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import platform_os
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
        if sys.platform == "win32":
            with patch.object(platform_os, "open_file_in_os") as launch:
                server.ChatHandler.open_workspace_file(handler)
            return handler.send_json.call_args.args[0], launch
        with patch.object(server.subprocess, "run") as launch:
            server.ChatHandler.open_workspace_file(handler)
        return handler.send_json.call_args.args[0], launch

    def test_html_uses_browser_and_executable_is_revealed(self):
        page = self.workspace.path / "index with spaces.html"
        page.write_text("<h1>Study guide</h1>")
        code = self.workspace.path / ("test.bat" if sys.platform == "win32" else "test.command")
        code.write_text("@echo off\n" if sys.platform == "win32" else "#!/bin/sh\n")
        if sys.platform != "win32":
            code.chmod(0o755)
        status, launch = self.open(str(page))
        self.assertEqual(status, 200)
        if sys.platform == "win32":
            launch.assert_called_once_with(page, reveal=False)
        else:
            self.assertEqual(launch.call_args.args[0], ["/usr/bin/open", "-b", "com.apple.Safari", str(page)])
        status, launch = self.open("./" + code.name)
        self.assertEqual(status, 200)
        if sys.platform == "win32":
            launch.assert_called_once_with(code, reveal=True)
        else:
            self.assertEqual(launch.call_args.args[0], ["/usr/bin/open", "-R", str(code)])

    def test_path_and_grant_are_checked_before_launch(self):
        outside = self.parent / "outside.html"
        outside.write_text("Outside")
        (self.root / ".env").write_text("Private")
        bad_paths = ["../outside.html", ".env", str(outside), "missing.html"]
        try:
            (self.root / "alias.html").symlink_to(outside)
            bad_paths.append("alias.html")
        except OSError:
            pass
        try:
            os.link(outside, self.root / "hard-link.html")
            if sys.platform == "win32":
                # Windows keeps the single-link rule (cloud sync clients there do not hard-link).
                bad_paths.append("hard-link.html")
            else:
                # A second hard link to a file you own (as Google Drive makes while syncing) is an ordinary workspace file.
                status, launch = self.open("hard-link.html")
                self.assertEqual(status, 200)
                self.assertIn(str(self.workspace.path / "hard-link.html"), [str(arg) for arg in launch.call_args.args[0]])
        except OSError:
            pass
        for path in bad_paths:
            with self.subTest(path=path):
                status, launch = self.open(path)
                self.assertEqual(status, 404)
                launch.assert_not_called()
        for fields in ({"key": "a" * 32}, {"root": str(outside.parent)}):
            status, launch = self.open("index.html", **fields)
            self.assertEqual(status, 403)
            launch.assert_not_called()
