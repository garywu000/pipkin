import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
