"""Problems found in a live Qwen 27B run: synced folders, sandbox here-documents, and a run view that fell behind."""
import json
import os
from pathlib import Path
import sys
from threading import Condition, Event
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import agent
from agent import Workspace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))


@unittest.skipIf(sys.platform == "win32", "hard links from sync clients are a macOS/Linux concern")
class SyncedFolderTest(unittest.TestCase):
    """Google Drive and similar clients hard-link files while uploading them."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.root = base / "project"
        self.root.mkdir()
        (self.root / "notes.md").write_text("original\n")
        self.sync_copy = base / ".tmp.driveupload-123"
        os.link(self.root / "notes.md", self.sync_copy)
        self.workspace = Workspace(self.root)

    def test_reads_a_file_the_sync_client_linked(self):
        self.assertIn("original", self.workspace.read_file("notes.md", 1, 5))

    def test_writes_replace_the_file_instead_of_writing_through_the_link(self):
        self.workspace.write_text("notes.md", "changed\n")
        self.assertEqual((self.root / "notes.md").read_text(), "changed\n")
        self.assertEqual(self.sync_copy.read_text(), "original\n", "the other link must keep its own content")
        self.assertEqual(os.stat(self.root / "notes.md").st_nlink, 1)
        self.assertFalse([p for p in self.root.iterdir() if p.name.startswith(".pipkin-")])

    def test_links_to_other_users_files_stay_refused(self):
        foreign = SimpleNamespace(st_nlink=2, st_uid=os.getuid() + 1)
        self.assertFalse(agent.ordinary_link(foreign))
        self.assertTrue(agent.ordinary_link(SimpleNamespace(st_nlink=2, st_uid=os.getuid())))
        self.assertTrue(agent.ordinary_link(SimpleNamespace(st_nlink=1, st_uid=0)))


@unittest.skipUnless(sys.platform == "darwin", "the Seatbelt sandbox is macOS-only")
class SandboxHereDocTest(unittest.TestCase):
    def test_here_documents_work_in_the_command_sandbox(self):
        with TemporaryDirectory() as directory:
            output = agent.run_command(Workspace(Path(directory)), "python3 - <<'EOF'\nprint('heredoc ok')\nEOF", lambda: None)
        self.assertIn("heredoc ok", output)
        self.assertNotIn("can't create temp file", output)


class RunEventLogTest(unittest.TestCase):
    """The page can fetch events it missed, and a canceled approval says why."""

    def run_scripted(self, on_event):
        import run as bench
        import server
        steps = iter([{"tool_calls": [{"function": {"name": "run_command", "arguments": {"command": "echo one"}}}]},
                      {"content": "Done."}])

        def step(_handler, messages, _tools, _think, _check, emit, **options):
            emit("step")
            if options.get("output_schema") is not None:
                return {"content": json.dumps({"route": "agent"})}
            return next(steps, {"content": "Done."})

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ws").mkdir()
            sessions = server.BrowserSessions()
            with patch.object(server, "CURRENT", {"model": "replay", "num_ctx": 8192}), \
                    patch.object(server, "SETTINGS_FILE", root / "settings.json"), \
                    patch.object(server, "SESSIONS", sessions), patch.object(server, "WORKSPACES", {}), \
                    patch.object(sessions, "ensure_loaded"), patch.object(server.ChatHandler, "agent_step", step):
                key = server.grant_workspace(server.Workspace(root / "ws"))
                options = {"task": "Check.", "workspace": key, "mode": "ask", "web": False, "allowMemory": False, "think": False,
                           "maxSteps": 6, "allowShell": True, "checkpoint": str(uuid4())}
                streamed, run_id = [], None
                with bench.request("/api/agent/run", options, timeout=30) as response:
                    for line in response:
                        event = json.loads(line)
                        streamed.append(event)
                        if event["event"] == "run":
                            run_id = event["id"]
                        if on_event(event, run_id, bench) is False:
                            break
                return streamed, run_id, bench

    def test_events_are_numbered_and_replayable_after_the_run(self):
        def approve(event, run_id, bench):
            if event["event"] == "approval":
                bench.post("/api/agent/approve", {"run": run_id, "id": event["id"], "approved": True})

        streamed, run_id, bench = self.run_scripted(approve)
        seqs = [event["seq"] for event in streamed]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        replay = bench.post("/api/agent/events", {"run": run_id, "after": 0})
        self.assertTrue(replay["done"])
        self.assertEqual(replay["events"], streamed)
        tail = bench.post("/api/agent/events", {"run": run_id, "after": len(streamed) - 2})
        self.assertEqual(tail["events"], streamed[-2:])
        with self.assertRaises(RuntimeError):
            bench.post("/api/agent/events", {"run": "x" * 16, "after": 0})

    def test_steering_during_an_approval_explains_the_cancel(self):
        def steer(event, run_id, bench):
            if event["event"] == "approval":
                bench.post("/api/agent/steer", {"run": run_id, "id": str(uuid4()), "text": "Do something else."})

        streamed, _, _ = self.run_scripted(steer)
        result = next(event for event in streamed if event["event"] == "approval_result")
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["reason"], "steering")
        tool = next(event for event in streamed if event["event"] == "tool_result")
        self.assertTrue(tool["cancelled"])

    def test_disconnect_while_waiting_records_a_resumable_stop(self):
        streamed, run_id, bench = self.run_scripted(lambda event, *_: event["event"] != "approval")
        self.assertEqual(streamed[-1]["event"], "approval")
        after = streamed[-1]["seq"]
        deadline = time.monotonic() + 4
        while True:
            replay = bench.post("/api/agent/events", {"run": run_id, "after": after})
            if replay["done"]:
                break
            self.assertLess(time.monotonic(), deadline, "the disconnected run should release its approval")
            time.sleep(0.025)
        self.assertEqual(replay["pending"], [])
        self.assertEqual([event["event"] for event in replay["events"]], ["stopped"])
        self.assertIn("Resume Work", replay["events"][0]["text"])

    def test_retry_can_confirm_but_cannot_replace_pending_decision(self):
        import run as bench
        import server

        run_id, call_id = "test-approval-id", "one"
        state = {"changed": Condition(), "pending": {call_id}, "decisions": {},
                 "stop_after_step": Event(), "steering_seen": 0, "steering": []}
        request = {"run": run_id, "id": call_id, "approved": False}
        with patch.dict(server.RUNS, {run_id: state}):
            self.assertTrue(bench.post("/api/agent/approve", request)["ok"])
            self.assertTrue(bench.post("/api/agent/approve", request)["ok"])
            with self.assertRaisesRegex(RuntimeError, "HTTP 409"):
                bench.post("/api/agent/approve", {**request, "approved": True})
        self.assertIs(state["decisions"][call_id], False)

    def test_structural_events_survive_long_thinking_bursts(self):
        import run as bench
        import server

        def step(_handler, messages, _tools, _think, _check, emit, **options):
            emit("step")
            if options.get("output_schema") is not None:
                return {"content": json.dumps({"route": "agent"})}
            for i in range(40):
                emit("thinking", text=f"tok{i} ")
            return {"content": "Finished cleanly."}

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ws").mkdir()
            sessions = server.BrowserSessions()
            with patch.object(server, "RUN_LOG_LIMIT", 12), \
                    patch.object(server, "CURRENT", {"model": "replay", "num_ctx": 8192}), \
                    patch.object(server, "SETTINGS_FILE", root / "settings.json"), \
                    patch.object(server, "SESSIONS", sessions), patch.object(server, "WORKSPACES", {}), \
                    patch.object(sessions, "ensure_loaded"), patch.object(server.ChatHandler, "agent_step", step):
                key = server.grant_workspace(server.Workspace(root / "ws"))
                options = {"task": "Think long.", "workspace": key, "mode": "ask", "web": False, "allowMemory": False, "think": True,
                           "maxSteps": 4, "allowShell": False, "checkpoint": str(uuid4())}
                run_id = None
                with bench.request("/api/agent/run", options, timeout=30) as response:
                    for line in response:
                        event = json.loads(line)
                        if event["event"] == "run":
                            run_id = event["id"]
                replay = bench.post("/api/agent/events", {"run": run_id, "after": 0})
                kinds = [ev["event"] for ev in replay["events"]]
                self.assertIn("run", kinds)
                self.assertIn("result", kinds)
                self.assertIn("usage", kinds)
                self.assertFalse(replay["gap"])


class RepeatedCommandFailureTest(unittest.TestCase):
    """Failed shell commands should not re-prompt identically or loop blindly."""

    def test_failed_command_is_not_reapproved_until_workspace_changes(self):
        with TemporaryDirectory() as directory:
            ws_dir = Path(directory) / "work"
            ws_dir.mkdir()
            ws = Workspace(ws_dir)
            approvals = []
            seen = []
            cmd = f'{("python" if sys.platform == "win32" else "python3")} -c "import sys; sys.exit(1)"'
            replies = iter([
                {"tool_calls": [{"function": {"name": "run_command", "arguments": {"command": cmd}}}]},
                {"tool_calls": [{"function": {"name": "run_command", "arguments": {"command": cmd}}}]},
                {"tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "fix.txt", "content": "ok\n"}}}]},
                {"tool_calls": [{"function": {"name": "run_command", "arguments": {"command": cmd}}}]},
                {"content": "Done."},
            ])

            def model(messages, _tools, _think):
                seen.append(messages)
                return next(replies)

            work = agent.Agent(
                model, lambda *_a, **_k: None, lambda: None,
                lambda *a: approvals.append(a) or True,
                workspace=ws, mode="auto", web=False, allow_shell=True,
            )
            work.run("System", [], "Run a check.")
            cmd_approvals = [a for a in approvals if a[0] == "command"]
            self.assertEqual(len(cmd_approvals), 2, "duplicate failed command before any edit must not re-prompt")
            tool_msgs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
            self.assertIn("already failed with no workspace changes", tool_msgs[1])

    def test_consecutive_different_command_failures_nudge_switch_and_hints(self):
        with TemporaryDirectory() as directory:
            ws_dir = Path(directory) / "work"
            ws_dir.mkdir()
            ws = Workspace(ws_dir)
            notices = []
            seen = []
            py = "python" if sys.platform == "win32" else "python3"
            replies = iter([
                {"tool_calls": [{"function": {"name": "run_command", "arguments": {"command": f'{py} -c "import nonexistent_pkg_xyz"'}}}]},
                {"tool_calls": [{"function": {"name": "run_command", "arguments": {"command": f'{py} -c "raise RuntimeError(1)"'}}}]},
                {"content": "Switched approach."},
            ])

            def model(messages, _tools, _think):
                seen.append(messages)
                return next(replies)

            work = agent.Agent(
                model, lambda ev, **d: notices.append(d.get("text", "")) if ev == "notice" else None,
                lambda: None, lambda *_a: True,
                workspace=ws, mode="auto", web=False, allow_shell=True,
            )
            work.run("System", [], "Inspect.")
            tool_msgs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
            self.assertIn("read_file already reads PDFs", tool_msgs[0])
            self.assertIn("2 shell commands failed in a row", tool_msgs[1])
            self.assertTrue(any("2 commands failed in a row" in n for n in notices))

    def test_command_that_fails_to_start_does_not_prompt_twice(self):
        with TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            call = {"tool_calls": [{"function": {"name": "run_command", "arguments": {"command": "echo test"}}}]}
            replies = iter([call, call, {"content": "The check could not start."}])
            approvals = []
            work = agent.Agent(lambda *_: next(replies), lambda *_a, **_k: None, lambda: None,
                               lambda *details: approvals.append(details) or True,
                               workspace=workspace, mode="auto", web=False, allow_shell=True)
            with patch.object(agent, "run_command", side_effect=OSError("Test process could not start")) as command:
                work.run("System", [], "Run a check.")
            self.assertEqual(command.call_count, 1)
            self.assertEqual(len(approvals), 1)


if __name__ == "__main__":
    unittest.main()
