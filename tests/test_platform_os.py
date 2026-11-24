import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import agent
import artifacts
import platform_os


class PlatformOsTests(unittest.TestCase):
    def test_app_data_dir_per_platform(self):
        mac = platform_os.app_data_dir(platform_name="darwin", env={}, home="/Users/alex")
        self.assertEqual(mac, Path("/Users/alex/Library/Application Support/Pipkin"))

        win = platform_os.app_data_dir(
            platform_name="win32",
            env={"LOCALAPPDATA": "/Users/alex/AppData/Local"},
            home="/Users/alex",
        )
        self.assertEqual(win, Path("/Users/alex/AppData/Local/Pipkin"))

        win_fallback = platform_os.app_data_dir(platform_name="win32", env={}, home="/Users/alex")
        self.assertEqual(win_fallback, Path("/Users/alex/AppData/Local/Pipkin"))

        linux = platform_os.app_data_dir(
            platform_name="linux",
            env={"XDG_DATA_HOME": "/home/alex/.data"},
            home="/home/alex",
        )
        self.assertEqual(linux, Path("/home/alex/.data/pipkin"))

    def test_ollama_candidates_windows_and_macos(self):
        mac = platform_os.ollama_candidates(platform_name="darwin", env={})
        self.assertIn("/opt/homebrew/bin/ollama", mac)
        self.assertIn("/Applications/Ollama.app/Contents/Resources/ollama", mac)

        win = platform_os.ollama_candidates(
            platform_name="win32",
            env={"LOCALAPPDATA": "C:/Users/Alex/AppData/Local", "ProgramFiles": "C:/Program Files"},
        )
        self.assertTrue(any("Programs" in p and p.endswith("ollama.exe") for p in win))
        self.assertTrue(any("Program Files" in p and p.endswith("ollama.exe") for p in win))

    def test_parse_windows_memory_status_thresholds_and_swap(self):
        gib = 1024 ** 3
        normal = platform_os.parse_windows_memory_status(
            memory_load=52,
            total_phys=16 * gib,
            avail_phys=8 * gib,
            total_page=20 * gib,
            avail_page=11 * gib,
        )
        self.assertEqual(normal["pressure"], "normal")
        self.assertEqual(normal["total_ram"], 16 * gib)
        self.assertEqual(normal["memory"]["used"], 8 * gib)
        self.assertEqual(normal["memory"]["used_pct"], 52)
        self.assertEqual(normal["memory"]["swap_total"], 4 * gib)
        self.assertEqual(normal["memory"]["swap_used"], 1 * gib)

        warn = platform_os.parse_windows_memory_status(
            memory_load=84,
            total_phys=16 * gib,
            avail_phys=2 * gib,
            total_page=16 * gib,
            avail_page=2 * gib,
        )
        self.assertEqual(warn["pressure"], "warning")

        crit = platform_os.parse_windows_memory_status(
            memory_load=95,
            total_phys=16 * gib,
            avail_phys=1 * gib,
            total_page=16 * gib,
            avail_page=1 * gib,
        )
        self.assertEqual(crit["pressure"], "critical")

    def test_keep_awake_noop_stop_is_safe(self):
        keeper = platform_os.KeepAwake()
        keeper.stop()

    def test_pure_python_pdf_render_and_extract_roundtrip(self):
        blocks = [
            {"kind": "title", "text": "Windows Parity Report"},
            {"kind": "subtitle", "text": "Generated without external PDF binaries"},
            {"kind": "heading", "text": "Key Findings"},
            {"kind": "bullet", "text": "Workspace edits are atomic and validated."},
            {"kind": "bullet", "text": "PDF extraction preserves page headers."},
            {"kind": "body", "text": "Smart quotes ‘work’ — dashes and bullets too."},
        ]
        pdf_bytes = platform_os.render_pdf_blocks_pure(blocks)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-1.4"))
        self.assertIn(b"%%EOF", pdf_bytes)

        extracted = platform_os.extract_pdf_pages_pure(pdf_bytes)
        self.assertIn("## PDF page 1", extracted)
        self.assertIn("Windows Parity Report", extracted)
        self.assertIn("Key Findings", extracted)
        self.assertIn("Workspace edits are atomic and validated.", extracted)

    def test_artifacts_render_pdf_fallback_without_mac_helper(self):
        plan = {
            "path": "summary.pdf",
            "title": "Study Summary",
            "intro": "Key notes for chapter one.",
            "sections": [{"heading": "Core Ideas", "items": ["Local execution"], "sources": []}],
        }
        with patch.object(artifacts.sys, "platform", "win32"):
            pdf = artifacts.render("pdf", plan, {})
        self.assertTrue(pdf.startswith(b"%PDF-"))
        text = platform_os.extract_pdf_pages_pure(pdf)
        self.assertIn("Study Summary", text)
        self.assertIn("Core Ideas", text)

    def test_windows_path_validation_security(self):
        self.assertEqual(agent.validate_path(r"notes\chapter1.md"), ["notes", "chapter1.md"])
        for bad in (
            r"..\outside.txt",
            r"sub\..\..\outside.txt",
            r"C:\Windows\System32\cmd.exe",
            "C:secret.txt",
            r"\\server\share\file.txt",
            "notes.txt:stream",
            "CON",
            "nul.txt",
            "COM1",
            "LPT2.md",
            "trailing-dot.",
            "sub /notes.txt",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    agent.validate_path(bad)

    def test_windows_workspace_mode_crud_and_safety(self):
        with TemporaryDirectory(prefix="pipkin-win-ws-") as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("secret", encoding="utf-8")

            ws = agent.Workspace(root)
            ws._windows = True  # Exercise Windows path logic even when running tests on macOS

            ws.write_text("sub/notes.md", "Alpha line\nBeta line\n")
            self.assertIn("notes.md", ws.list_files(".", lambda: None))
            self.assertIn("Alpha line", ws.read_file("sub/notes.md", 1, 5))
            # Single-file search_text relies on NotADirectoryError parity
            self.assertIn("Beta line", ws.search_text("sub/notes.md", "Beta", lambda: None))

            before_digest = ws.file_digest("sub/notes.md")
            ws.write_text("sub/notes.md", "Changed\n")
            changed_digest = ws.file_digest("sub/notes.md")
            self.assertNotEqual(before_digest, changed_digest)
            ws.restore_text("sub/notes.md", "Alpha line\nBeta line\n", changed_digest)
            self.assertEqual(ws.file_digest("sub/notes.md"), before_digest)

            ws.write_new_artifact("out/report.txt", b"Created once")
            with self.assertRaises(ValueError):
                ws.write_new_artifact("out/report.txt", b"Duplicate")
            ws.remove_file("out/report.txt")
            self.assertIsNone(ws.file_digest("out/report.txt"))

            # Symlink escape check (skip if OS lacks symlink privilege)
            try:
                (root / "escape.txt").symlink_to(outside)
            except OSError:
                pass
            else:
                with self.assertRaises(ValueError):
                    ws.read_file("escape.txt", 1, 5)

    def test_windows_isolated_env_and_scope(self):
        with TemporaryDirectory(prefix="pipkin-win-env-") as tmp:
            root = Path(tmp) / "work"
            root.mkdir()
            ws = agent.Workspace(root)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret", "GITHUB_TOKEN": "ghp_secret"}, clear=False):
                env = agent.isolated_env(tmp, platform_name="win32")
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertEqual(env["USERPROFILE"], tmp)
            self.assertEqual(env["LOCALAPPDATA"], tmp)
            self.assertEqual(env["TEMP"], tmp)

            scope = agent.describe_command_scope("Remove-Item notes.md", ws, platform_name="win32")
            self.assertIn("May modify files", scope)
            self.assertIn("Working dir: work", scope)
            self.assertNotIn("No network", scope)


if __name__ == "__main__":
    unittest.main()
