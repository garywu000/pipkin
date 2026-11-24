import unittest

import server


class TelemetryTest(unittest.TestCase):
    def test_pressure_level_mapping(self):
        self.assertEqual(server.parse_pressure_level("1\n"), "normal")
        self.assertEqual(server.parse_pressure_level("2"), "warning")
        self.assertEqual(server.parse_pressure_level("4"), "critical")
        self.assertEqual(server.parse_pressure_level(""), "unknown")
        self.assertEqual(server.parse_pressure_level("99"), "unknown")

    def test_parse_running_models_filters_and_normalizes(self):
        payload = {
            "models": [
                {"name": "qwen3:8b", "size": 5_400_000_000, "size_vram": 5_400_000_000, "context_length": 16384, "expires_at": "2026-11-23T10:00:00Z"},
                {"name": "", "size": 100},
                "invalid",
                {"name": "qwen3:27b", "size": -5, "size_vram": None},
            ],
        }
        parsed = server.parse_running_models(payload)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name"], "qwen3:8b")
        self.assertEqual(parsed[0]["size"], 5_400_000_000)
        self.assertEqual(parsed[0]["context_length"], 16384)
        self.assertEqual(parsed[1]["name"], "qwen3:27b")
        self.assertEqual(parsed[1]["size"], 0)
        self.assertEqual(parsed[1]["size_vram"], 0)
        self.assertEqual(server.parse_running_models(None), [])

    def test_recommend_num_ctx_scales_with_headroom(self):
        gib = 1024 ** 3
        self.assertEqual(server.recommend_num_ctx(16 * gib, 12 * gib), 4096)
        self.assertEqual(server.recommend_num_ctx(16 * gib, 8 * gib), 8192)
        self.assertEqual(server.recommend_num_ctx(24 * gib, 10 * gib), 16384)
        self.assertEqual(server.recommend_num_ctx(48 * gib, 20 * gib), 32768)
        self.assertEqual(server.recommend_num_ctx(64 * gib, 20 * gib), 65536)
        self.assertIsNone(server.recommend_num_ctx(0, 10 * gib))

    def test_memory_guard_warns_once_and_pauses_on_sustained_critical(self):
        now = [0.0]
        levels = iter(["normal", "warning", "warning", "critical", "normal", "critical", "critical"])
        guard = server.MemoryGuard(enabled=True, interval=3.0, critical_hits=2,
                                   sampler=lambda: next(levels), clock=lambda: now[0])
        self.assertEqual(guard.check(), (None, None))
        now[0] = 3.0
        notice, pause = guard.check()
        self.assertIn("elevated", notice)
        self.assertIsNone(pause)
        now[0] = 6.0
        self.assertEqual(guard.check(), (None, None), "warning notice should only fire once")
        now[0] = 9.0
        self.assertEqual(guard.check(), (None, None), "single critical sample should not pause yet")
        now[0] = 12.0
        self.assertEqual(guard.check(), (None, None), "normal resets critical streak")
        now[0] = 15.0
        self.assertEqual(guard.check(), (None, None), "first critical of new streak")
        now[0] = 18.0
        _, pause = guard.check()
        self.assertIn("paused safely before swapping", pause)
        self.assertTrue(guard.paused)

    def test_memory_guard_disabled_never_samples(self):
        called = []
        guard = server.MemoryGuard(enabled=False, sampler=lambda: called.append(1) or "critical")
        self.assertEqual(guard.check(), (None, None))
        self.assertEqual(called, [])

    def test_ensure_loaded_refuses_cold_load_under_critical_pressure(self):
        from unittest.mock import patch
        sessions = server.BrowserSessions()
        with patch.object(server, "current", return_value={"model": "qwen3:27b", "num_ctx": 16384}), \
             patch.object(server, "load_settings", return_value={"protect_memory": True}), \
             patch.object(server, "memory_pressure", return_value="critical"):
            with self.assertRaisesRegex(ValueError, "memory pressure is critical"):
                sessions.ensure_loaded()

    def test_parse_vm_stat_and_swap_usage(self):
        sample_vm = (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages wired down: 100000.\n"
            "Pages purgeable: 10000.\n"
            "File-backed pages: 50000.\n"
            "Anonymous pages: 210000.\n"
            "Pages occupied by compressor: 50000.\n"
        )
        info = server.parse_vm_stat(sample_vm)
        self.assertEqual(info["app"], 200000 * 16384)
        self.assertEqual(info["wired"], 100000 * 16384)
        self.assertEqual(info["compressed"], 50000 * 16384)
        self.assertEqual(info["cached"], 60000 * 16384)
        self.assertEqual(info["used"], 350000 * 16384)
        swap = server.parse_swap_usage("total = 4096.00M  used = 2048.00M  free = 2048.00M  (encrypted)")
        self.assertEqual(swap["swap_total"], 4096 * 1024 * 1024)
        self.assertEqual(swap["swap_used"], 2048 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
