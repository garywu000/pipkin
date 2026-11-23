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


if __name__ == "__main__":
    unittest.main()
