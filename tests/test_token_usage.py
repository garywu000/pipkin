from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import server


def reply_stream(*packets):
    response = BytesIO(b"".join(json.dumps(packet).encode() + b"\n" for packet in packets))
    response.status = 200
    return Mock(), response


def final_packet(prompt, generated):
    return {"done": True, "prompt_eval_count": prompt, "eval_count": generated}


class TokenUsageTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        usage_file = patch.object(server, "USAGE_FILE", Path(folder.name) / "usage.sqlite3")
        usage_file.start()
        self.addCleanup(usage_file.stop)

    def test_completed_chat_compaction_work_and_web_rounds_all_count(self):
        handler = server.ChatHandler.__new__(server.ChatHandler)
        handler.headers_for = Mock()
        handler.wfile = BytesIO()
        message = [{"role": "user", "content": "A sample prompt"}]
        search = {"function": {"name": "web_search", "arguments": {"query": "sample query"}}}
        streams = [
            reply_stream({"message": {"content": "Hello"}}, final_packet(30, 8)),
            reply_stream(final_packet(40, 7)),
            reply_stream({"message": {"content": "Work result"}}, final_packet(60, 11)),
            reply_stream({"message": {"tool_calls": [search]}}, final_packet(50, 5)),
            reply_stream(final_packet(70, 9)),
        ]
        with patch.object(server, "ollama_request", side_effect=streams), patch.object(server, "web_search", return_value="Sample result"):
            handler.relay_chat(message, False)
            handler.relay_chat(message, False, compact=True)
            step = handler.agent_step(message, [], False, lambda: None, lambda *_args, **_fields: None)
            handler.relay_chat_with_web(message, False, "", None, "on")
        self.assertEqual(step["eval_count"], 11)
        self.assertEqual(server.token_usage(), {"input": 250, "output": 40})

    def test_partial_streams_and_invalid_metrics_are_skipped(self):
        _, interrupted = reply_stream({"message": {"content": "Partial"}, "eval_count": 8})
        list(server.ollama_chat_packets(interrupted))
        self.assertEqual(server.token_usage(), {"input": 0, "output": 0})
        self.assertFalse(server.USAGE_FILE.exists())

        _, repeated = reply_stream(final_packet(-20, 6), final_packet(100, 10))
        self.assertEqual(len(list(server.ollama_chat_packets(repeated))), 1)
        self.assertEqual(server.token_usage(), {"input": 0, "output": 6})

    def test_concurrent_writes_do_not_lose_counts(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: server.token_usage(12, 3), range(24)))
        self.assertEqual(server.token_usage(), {"input": 288, "output": 72})
