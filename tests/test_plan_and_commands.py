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
        import sys
        idle = 3 if sys.platform == "win32" else 1
        started = time.monotonic()
        with patch.object(agent, "COMMAND_IDLE", idle):
            result = agent.run_command(self.workspace, "echo starting; sleep 30", lambda: None)
        self.assertLess(time.monotonic() - started, 10 if sys.platform == "win32" else 5)
        self.assertIn("starting", result)
        self.assertIn(f"printed nothing for {idle}s", result)
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

    def test_builtin_tool_name_in_run_command_is_refused_before_approval(self):
        approvals = []
        replies = iter([
            {"tool_calls": [tool("run_command", command="check_page index.html")]},
            {"content": "I will call check_page directly."},
            {"content": "Checked."},
        ])
        seen = []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda *_, **__: None, lambda: None, lambda *a: approvals.append(a) or True,
              workspace=self.workspace, mode="ask", web=False).run("System", [], "Verify index.html.")
        self.assertEqual(approvals, [], "built-in tool mistake should be caught before asking for shell approval")
        tool_msg = next(m["content"] for m in seen[-1] if m["role"] == "tool")
        self.assertIn("built-in Pipkin tool", tool_msg)
        self.assertIn("`check_page`", tool_msg)

    def test_check_page_is_available_in_default_workspace_mode(self):
        tools = agent.available_tools(self.workspace, mode="ask", web=False, memory=False, workflow=None)
        names = {item["function"]["name"] for item in tools}
        self.assertIn("check_page", names)

    def test_unavailable_builtin_tool_in_run_command_says_not_enabled(self):
        replies = iter([
            {"tool_calls": [tool("run_command", command="web_search python")]},
            {"content": "Skipping web search."},
        ])
        seen = []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda *_, **__: None, lambda: None, lambda *_: True,
              workspace=self.workspace, mode="ask", web=False).run("System", [], "Check docs.")
        tool_msg = next(m["content"] for m in seen[-1] if m["role"] == "tool")
        self.assertIn("not enabled in this session", tool_msg)
        self.assertNotIn("Call the `web_search` tool directly", tool_msg)

    def test_duplicate_run_command_is_skipped_until_workspace_changes(self):
        approvals = []
        replies = iter([
            {"tool_calls": [tool("run_command", command="echo ok")]},
            {"tool_calls": [tool("run_command", command="echo ok")]},
            {"tool_calls": [tool("write_file", path="note.txt", content="hi\n")]},
            {"tool_calls": [tool("run_command", command="echo ok")]},
            {"content": "Done."},
        ])
        seen = []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda *_, **__: None, lambda: None, lambda *a: approvals.append(a) or True,
              workspace=self.workspace, mode="auto", web=False).run("System", [], "Run check.")
        cmd_approvals = [a for a in approvals if a[0] == "command"]
        self.assertEqual(len(cmd_approvals), 2, "second identical command before any edit should not re-prompt")
        tool_msgs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
        self.assertIn("already ran with no workspace changes", tool_msgs[1])

    def test_near_repeat_commands_are_flagged_then_skipped_until_workspace_changes(self):
        # Seen with Qwen: the same PDF script rerun with one more "#" in its banner each time, same text out.
        body = "Circuit Workshop. Workshop meets Thursday at 4:00 p.m. Bring a notebook."
        command = lambda marks: f"python3 -c \"print('{'#' * marks} stream 0'); print('{body}')\""
        approvals = []
        replies = iter([
            {"tool_calls": [tool("run_command", command=command(5))]},
            {"tool_calls": [tool("run_command", command=command(6))]},
            {"tool_calls": [tool("run_command", command=command(7))]},
            {"tool_calls": [tool("write_file", path="summary.md", content="Thursday 4:00 p.m.\n")]},
            {"tool_calls": [tool("run_command", command=command(8))]},
            {"content": "Done."},
        ])
        seen, notices = [], []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        def emit(event, **fields):
            if event == "notice":
                notices.append(fields["text"])

        Agent(model, emit, lambda: None, lambda *a: approvals.append(a) or True,
              workspace=self.workspace, mode="auto", web=False).run("System", [], "Summarize the packet.")
        tool_msgs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
        self.assertNotIn("same result", tool_msgs[0])
        self.assertIn("same result an earlier command already returned", tool_msgs[1])
        self.assertTrue(tool_msgs[2].startswith("Not run:"), tool_msgs[2])
        self.assertEqual(len([a for a in approvals if a[0] == "command"]), 3, "the skipped command is never run")
        self.assertIn(body, tool_msgs[4], "a file change clears the repeat history")
        self.assertNotIn("same result", tool_msgs[4])
        self.assertTrue(any("Skipped a command" in text for text in notices))

    def test_commands_that_change_files_are_not_repeats_even_with_the_same_output(self):
        banner = "Saving the generated report into the workspace folder now."
        replies = iter([{"tool_calls": [tool("run_command", command=f"echo {banner}; echo {n} > out{n}.txt")]} for n in range(3)]
                       + [{"content": "Done."}])
        seen = []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda *_, **__: None, lambda: None, lambda *a: True,
              workspace=self.workspace, mode="auto", web=False).run("System", [], "Write three files.")
        tool_msgs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
        self.assertFalse(any("same result" in text or text.startswith("Not run:") for text in tool_msgs), tool_msgs)
        self.assertEqual(sorted(p.name for p in self.path.glob("out*.txt")), ["out0.txt", "out1.txt", "out2.txt"])

    def test_different_commands_with_tiny_outputs_are_not_repeats(self):
        replies = iter([
            {"tool_calls": [tool("run_command", command="mkdir -p a")]},
            {"tool_calls": [tool("run_command", command="mkdir -p b")]},
            {"tool_calls": [tool("run_command", command="mkdir -p c")]},
            {"content": "Done."},
        ])
        seen = []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        Agent(model, lambda *_, **__: None, lambda: None, lambda *a: True,
              workspace=self.workspace, mode="auto", web=False).run("System", [], "Make folders.")
        tool_msgs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
        self.assertFalse(any("same result" in text or text.startswith("Not run:") for text in tool_msgs), tool_msgs)

    def test_html_write_auto_runs_check_page_in_default_mode(self):
        self.workspace.write_text("notes.md", "# Notes\n")
        replies = iter([
            {"tool_calls": [tool("write_file", path="index.html", content='<a href="notes.md">Notes</a>')]},
            {"content": "Created index.html."},
        ])
        seen = []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        work = Agent(model, lambda *_, **__: None, lambda: None, lambda *_: True,
                     workspace=self.workspace, mode="auto", web=False)
        work.run("System", [], "Create index.html.")
        tool_msg = next(m["content"] for m in seen[-1] if m["role"] == "tool")
        self.assertIn("static link and anchor check", tool_msg)
        self.assertTrue(any(o["path"] == "index.html" and o.get("checks") for o in work.outputs))

    def test_agent_panels_keeps_browser_method_callable(self):
        import server
        panels = server.AgentPanels({"changed": None, "browser": {}}, lambda *_, **__: None, None, True, self.workspace.path)
        self.assertTrue(panels.has_browser)
        self.assertTrue(callable(panels.browser))
        names = {t["function"]["name"] for t in agent.available_tools(self.workspace, "ask", False, False, panels=panels)}
        self.assertIn("browser_open", names)
        self.assertIn("browser_read", names)
        self.assertIn("terminal_read", names)
        self.assertIn("terminal_run", names)
        restricted = server.AgentPanels({"changed": None, "browser": {}}, lambda *_, **__: None, None, False, self.workspace.path, allow_terminal=False)
        restricted_names = {t["function"]["name"] for t in agent.available_tools(self.workspace, "ask", False, False, panels=restricted)}
        self.assertNotIn("browser_open", restricted_names)
        self.assertNotIn("terminal_read", restricted_names)
        self.assertNotIn("terminal_run", restricted_names)

    def test_thought_watch_catches_repeated_content_output(self):
        import server
        watch = server.ThoughtWatch(window=120, limit=None, every=100, message="repeated output")
        chunk = "This exact sentence keeps repeating over and over again in the model output without making progress. "
        with self.assertRaisesRegex(server.ThinkingLoop, "repeated output"):
            for _ in range(10):
                watch.add(chunk)

    def test_working_memory_and_read_ledger_survive_compaction_and_checkpoints(self):
        self.workspace.write_text("brief.md", "# Project Alpha\nBudget is $42,000.\n" + ("Detail line.\n" * 180))
        replies = iter([
            {"tool_calls": [tool("read_file", path="brief.md", start_line=1, count=120)]},
            {"tool_calls": [tool("save_note", note="Budget is $42,000", file="brief.md")]},
            {"tool_calls": [tool("read_file", path="brief.md", start_line=121, count=100)]},
            {"tool_calls": [tool("list_files", path=".")]},
            {"content": "Summary ready."},
        ])
        seen = []
        saved_checkpoint = {}
        events = []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        work = Agent(model, lambda ev, **d: events.append((ev, d)), lambda: None, lambda *_: True,
                     workspace=self.workspace, mode="auto", web=False, num_ctx=1024)
        work.run("System", [], "Summarize brief.md.",
                 save_checkpoint=lambda **fields: saved_checkpoint.update(fields))
        self.assertTrue(any(ev == "notes" and d["notes"][0]["text"] == "Budget is $42,000" for ev, d in events))
        self.assertGreaterEqual(work.compactions, 1, "tight num_ctx should trigger fit() compaction")
        task_msg = seen[-1][1]["content"]
        self.assertIn("Working memory", task_msg)
        self.assertIn("[brief.md] Budget is $42,000", task_msg)
        self.assertIn("Read brief.md", task_msg)
        self.assertEqual(saved_checkpoint["working_notes"], [{"text": "Budget is $42,000", "file": "brief.md"}])
        self.assertIn("brief.md", saved_checkpoint["read_ledger"])

    def test_shell_opt_in_and_isolated_env_and_scope(self):
        import os
        tools_off = {t["function"]["name"] for t in agent.available_tools(self.workspace, "ask", False, False, allow_shell=False)}
        self.assertNotIn("run_command", tools_off)
        tools_on = {t["function"]["name"] for t in agent.available_tools(self.workspace, "ask", False, False, allow_shell=True)}
        self.assertIn("run_command", tools_on)

        old = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "ghp_secret_test_value"
        try:
            env = agent.isolated_env("/tmp/pipkin-test-tmp")
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertEqual(env["HOME"], "/tmp/pipkin-test-tmp")
            self.assertEqual(env["TMPDIR"], "/tmp/pipkin-test-tmp")
        finally:
            if old is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = old

        import sys
        scope = agent.describe_command_scope("rm -f notes.md && echo ok", self.workspace)
        self.assertIn("May modify files", scope)
        self.assertIn("Isolated to" if sys.platform == "darwin" else "Working dir:", scope)
        self.assertIn("notes.md", scope)
        ro_scope = agent.describe_command_scope("ls -la index.html", self.workspace)
        self.assertIn("Read-only inspection", ro_scope)
        self.assertIn("index.html", ro_scope)

    def test_sandbox_blocks_write_outside_workspace(self):
        import os, sys, tempfile
        if sys.platform != "darwin" or not os.path.isfile("/usr/bin/sandbox-exec"):
            self.skipTest("sandbox-exec only on macOS")
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "escape.txt")
            result = agent.run_command(self.workspace, f'touch "{target}"', lambda: None)
            self.assertFalse(os.path.exists(target), "sandbox must block writes outside workspace")
            self.assertNotIn("[exit code 0]", result)

    def test_two_model_explore_then_write_handoff(self):
        self.workspace.write_text("brief.md", "Title: Launch Plan\n")
        calls_log = []
        replies = iter([
            {"tool_calls": [tool("read_file", path="brief.md")]},
            {"content": "Found title Launch Plan in brief.md; ready for writer."},
            {"tool_calls": [tool("write_file", path="summary.md", content="# Launch Plan\n")]},
            {"content": "Created summary.md."},
        ])

        def model(messages, tools, _think, model_override=None):
            calls_log.append((model_override, {t["function"]["name"] for t in tools}))
            return next(replies)

        events = []
        work = Agent(model, lambda ev, **d: events.append((ev, d)), lambda: None, lambda *_: True,
                     workspace=self.workspace, mode="auto", web=False, explore_model="qwen3:4b")
        work.run("System", [], "Summarize brief.md into summary.md.")
        self.assertEqual(calls_log[0][0], "qwen3:4b")
        self.assertNotIn("write_file", calls_log[0][1], "explorer phase must only expose read/plan/note tools")
        self.assertEqual(calls_log[1][0], "qwen3:4b")
        self.assertIsNone(calls_log[2][0], "writer phase must switch back to primary model")
        self.assertIn("write_file", calls_log[2][1])
        self.assertTrue(any(ev == "notice" and "switching to primary model" in d["text"] for ev, d in events))
        self.assertEqual(self.workspace.existing_text("summary.md"), "# Launch Plan\n")
