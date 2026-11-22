from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import agent
from agent import Agent, Workspace


def tool(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class PlanFirstTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="pipkin-plan-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        (self.path / "brief.md").write_text("Meet Thursday.\n")
        self.workspace = Workspace(directory.name)

    def test_first_request_asks_for_a_plan_and_reminds_once(self):
        seen = []
        replies = iter([
            {"tool_calls": [tool("read_file", path="brief.md")]},
            {"tool_calls": [tool("list_files", path=".")]},
            {"content": "Done."},
        ])

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda *_a, **_k: None, lambda: None, lambda *_: True, workspace=self.workspace,
              mode="ask", web=False).run("System", [], "Summarize brief.md.")
        self.assertIn("begin your first reply with update_plan", seen[0][-1]["content"])
        tool_outputs = [message["content"] for message in seen[-1] if message["role"] == "tool"]
        self.assertIn("call update_plan", tool_outputs[0])
        self.assertNotIn("call update_plan", tool_outputs[1], "the reminder is sent once")
        self.assertEqual(seen[1][:3], seen[2][:3], "the planning note keeps the prompt prefix stable")

    def test_no_reminder_when_the_first_reply_plans(self):
        seen = []
        replies = iter([
            {"tool_calls": [tool("read_file", path="brief.md"),
                            tool("update_plan", steps=[{"text": "Read brief.md", "status": "in_progress"}])]},
            {"content": "Done."},
        ])

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda *_a, **_k: None, lambda: None, lambda *_: True, workspace=self.workspace,
              mode="ask", web=False).run("System", [], "Summarize brief.md.")
        self.assertFalse(any("call update_plan" in m["content"] for m in seen[-1] if m["role"] == "tool"))


class CommandRecoveryTest(PlanFirstTest):
    def test_never_ending_command_is_refused_before_approval(self):
        asked = []
        work = Agent(None, lambda *_a, **_k: None, lambda: None, lambda *a: asked.append(a) or True,
                     workspace=self.workspace, mode="ask", web=False)
        with self.assertRaisesRegex(ValueError, "check_page or read_file"):
            work.execute("run_command", {"command": "cd . && python3 -m http.server 8000"})
        self.assertEqual(asked, [])

    def test_silent_command_is_stopped_with_recovery_advice(self):
        started = time.monotonic()
        with patch.object(agent, "COMMAND_IDLE", 1):
            result = agent.run_command(self.workspace, "echo starting; sleep 30", lambda: None)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("starting", result)
        self.assertIn("printed nothing for 1s", result)
        self.assertIn("Do not run it again", result)

    test_first_request_asks_for_a_plan_and_reminds_once = None
    test_no_reminder_when_the_first_reply_plans = None


if __name__ == "__main__":
    unittest.main()


class RecoveryHintTest(unittest.TestCase):
    names = {"read_file", "list_files", "write_file"}

    def test_hints_match_the_error(self):
        cases = {
            ("read_file", "Tool error: No such file or folder."): "list_files",
            ("edit_file", "Tool error: old_text was not found exactly once in the file."): "copy the snippet exactly",
            ("web_fetch", "Tool error: The tool 'web_fetch' is not available right now."): "list_files, read_file, write_file",
            ("run_command", "zsh:1: command not found: python\n[exit code 127]"): "python3",
            ("run_command", "grep: x.txt: No such file or directory\n[exit code 2]"): "list_files",
            ("run_command", "\n[exit code 1]"): "grep exits 1",
            ("write_file", "The user declined this change. Do not retry it."): "Do not retry",
        }
        for (name, output), expected in cases.items():
            with self.subTest(output=output):
                self.assertIn(expected, agent.recovery_hint(name, output, self.names))
        self.assertEqual(agent.recovery_hint("run_command", "ok\n[exit code 0]", self.names), "")
        self.assertFalse(agent.command_failed("3\n[exit code 0]"))
        self.assertTrue(agent.command_failed("x\n[Stopped: the command printed nothing for 30s. ...]"))


class RepeatedFailureTest(PlanFirstTest):
    test_first_request_asks_for_a_plan_and_reminds_once = None
    test_no_reminder_when_the_first_reply_plans = None

    def test_same_failure_twice_asks_for_a_new_approach(self):
        events, seen = [], []
        replies = iter([
            {"tool_calls": [tool("read_file", path="missing.md")]},
            {"tool_calls": [tool("read_file", path="missing.md", start_line=2)]},
            {"content": "brief.md says Thursday."},
        ])

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda event, **data: events.append((event, data)), lambda: None, lambda *_: True,
              workspace=self.workspace, mode="ask", web=False).run("System", [], "Read missing.md.")
        outputs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
        self.assertIn("(Recovery: Check the exact path with list_files", outputs[0])
        self.assertIn("failed the same way before", outputs[1])
        self.assertTrue(any(event == "notice" and "Same problem twice" in data["text"] for event, data in events))
