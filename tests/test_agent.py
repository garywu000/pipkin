from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent import Agent, WorkPaused, Workspace
from server import ChatHandler


def tool(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class AgentTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="pipkin-agent-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.workspace = Workspace(directory.name)
        self.events = []

    def emit(self, event, **data):
        self.events.append((event, data))

    def test_resume_identifies_canceled_actions_as_unfinished_and_requires_approval(self):
        approvals = []
        (self.path / "plan.txt").write_text("Review notes.\n")
        replies = iter([
            {"tool_calls": [tool("write_file", path="checklist.txt", content="Bring a notebook.\n")]},
            {"content": "Both files are ready."},
        ])

        def model(messages, _tools, _think):
            self.assertIn("requested deliverables against confirmed results", messages[0]["content"])
            self.assertIn("request fresh approval", messages[0]["content"])
            self.assertIn("honor any action the user declined", messages[0]["content"])
            if not approvals:
                self.assertEqual(messages[-1]["role"], "user")
                self.assertIn("checklist.txt", messages[-1]["content"])
            return next(replies)

        def approve(kind, title, diff):
            approvals.append((kind, title))
            return True

        work = Agent(model, self.emit, lambda: None, approve, workspace=self.workspace, mode="ask", web=False)
        work.run("System", [], "Save the plan and checklist.", saved={
            "files": ["plan.txt"],
            "activity": [{"name": "write_file", "label": "Write checklist.txt", "cancelled": True}],
        })
        self.assertEqual(approvals, [("edit", "Create checklist.txt")])
        self.assertEqual((self.path / "plan.txt").read_text(), "Review notes.\n")
        self.assertEqual((self.path / "checklist.txt").read_text(), "Bring a notebook.\n")

    def test_resume_does_not_finish_on_an_unchecked_canceled_edit(self):
        checkpoint = {}
        replies = iter([
            {"tool_calls": [tool("read_file", path="checklist.txt")]},
            {"content": "I finished."},
            {"content": "I finished."},
        ])
        work = Agent(lambda *_: next(replies), self.emit, lambda: None, lambda *_: False,
                     workspace=self.workspace, mode="ask", web=False)
        with self.assertRaisesRegex(WorkPaused, "canceled edit still to review"):
            work.run("System", [], "Save a checklist.", saved={
                "activity": [{"name": "write_file", "label": "Write checklist.txt", "cancelled": True}],
            }, save_checkpoint=lambda **fields: checkpoint.update(fields))
        self.assertFalse((self.path / "checklist.txt").exists())
        self.assertNotIn("result", [event for event, _ in self.events])
        self.assertEqual([event for event, _ in self.events].count("model_step"), 3)
        self.assertEqual(checkpoint["pending_edit_path"], "checklist.txt")

    def test_prompt_stays_cacheable_and_identical_write_is_blocked(self):
        body = "<main>A long page</main>\n" * 700
        write = tool("write_file", path="index.html", content=body)
        read = tool("read_files", files=[{"path": "index.html", "count": 1}, {"path": ".env"}])
        (self.path / ".env").write_text("PRIVATE")
        replies = iter([
            {"content": "I’ll build the page.", "tool_calls": [write, read]},
            {"tool_calls": [write, read]},
            {"content": "The page exists."},
        ])
        turns = []

        def model(messages, _tools, think):
            turns.append((deepcopy(messages), think))
            return next(replies)

        work = Agent(model, self.emit, lambda: None, lambda *_: False, workspace=self.workspace,
                     mode="auto", web=False, memory=False, num_ctx=16384, think=True)
        work.run("System", [], "Build a page", save_checkpoint=lambda **_: None)
        self.assertEqual([think for _, think in turns], [True, False, False])
        self.assertEqual((self.path / "index.html").read_text(), body)
        stored = [call["function"]["arguments"]["content"] for message in turns[1][0]
                  for call in message.get("tool_calls", []) if call["function"]["name"] == "write_file"]
        self.assertEqual(len(stored), 1)
        self.assertLess(len(stored[0]), 360)
        self.assertNotIn("PRIVATE", str(turns))
        self.assertEqual(turns[2][0][:len(turns[1][0])], turns[1][0])
        self.assertTrue(any(event == "tool_result" and "already made this exact change" in data["preview"]
                            for event, data in self.events))

    def test_page_is_escaped_and_requires_the_same_approval_as_a_file_write(self):
        approvals = []

        def deny(kind, title, diff):
            approvals.append((kind, title, diff))
            return False

        arguments = {"path": "index.html", "title": "Cafe & repairs", "intro": "<script>alert(1)</script>",
                     "sections": [{"heading": "Hours", "text": "10 am to noon"}]}
        work = Agent(None, self.emit, lambda: None, deny, workspace=self.workspace, mode="ask")
        self.assertIn("declined", work.execute("write_page", arguments))
        self.assertFalse((self.path / "index.html").exists())
        self.assertEqual(approvals[0][:2], ("edit", "Create index.html"))
        self.assertIn("&lt;script&gt;", approvals[0][2])
        with self.assertRaises(ValueError):
            work.execute("write_page", {**arguments, "path": "../index.html"})

    def test_search_accepts_a_file_and_keeps_workspace_boundaries(self):
        (self.path / "packet-text.md").write_text("Introduction\nNext class: Tuesday\n")
        (self.path / "other.txt").write_text("Next class: Wednesday\n")
        matches = self.workspace.search_text("packet-text.md", "next class:", lambda: None)
        self.assertEqual(matches, "packet-text.md:2: Next class: Tuesday")
        self.assertIn("other.txt:1:", self.workspace.search_text(".", "next class:", lambda: None))
        with self.assertRaises(ValueError):
            self.workspace.search_text("../packet-text.md", "class", lambda: None)

    def test_site_workflow_maps_pages_and_checks_links_in_read_mode(self):
        (self.path / "brief.md").write_text("## PDF page 4\nNext class: Tuesday\n## PDF page 13\nNext class: Thursday\n")
        (self.path / "packet.pdf").write_bytes(b"%PDF fixture")
        (self.path / "index.html").write_text(
            '<h1 id="start">Packet</h1><a href="#start">Start</a>'
            '<a href="packet.pdf#page=4">Packet</a><a href="#missing">Missing</a>'
            '<img src="missing.png"><h2 id="start">Duplicate</h2>'
        )
        work = Agent(None, self.emit, lambda: None, lambda *_: False, workspace=self.workspace, mode="read", workflow="site")
        self.assertNotIn("write_page", work.tool_names)
        mapped = work.execute("map_document", {"path": "brief.md", "queries": ["Next class:"]})
        self.assertIn("p. 4, line 2", mapped)
        self.assertIn("p. 13, line 4", mapped)
        checked = work.execute("check_page", {"path": "index.html"})
        self.assertIn("Duplicate id: start", checked)
        self.assertIn("Missing page anchor: #missing", checked)
        self.assertIn("Unresolved local link or asset: missing.png", checked)
        self.assertNotIn("packet.pdf", checked)

    def test_site_retry_discards_truncated_tool_output(self):
        calls = []

        class Runner:
            def agent_step(self, messages, tools, think, check, emit, *, output_limit):
                calls.append((messages, think, output_limit))
                if len(calls) == 1:
                    return {"finish_reason": "length", "tool_calls": [tool("write_file", path="incomplete.html")]}
                return {"finish_reason": "stop", "tool_calls": [tool("write_file", path="index.html")]}

        result = ChatHandler.agent_model(Runner(), [{"role": "user", "content": "Build the site"}], [tool("write_file")],
                                         True, lambda: None, self.emit, workflow="site")
        self.assertEqual(result["tool_calls"], [tool("write_file", path="index.html")])
        self.assertEqual([values[1:] for values in calls], [(True, 3400), (False, 2400)])
        self.assertIn("one small, complete change", calls[1][0][-1]["content"])
        self.assertFalse((self.path / "incomplete.html").exists())

    def test_site_maps_named_source_before_first_model_step(self):
        (self.path / "packet-text.md").write_text("## PDF page 6\nNext class: Monday\n")
        prompts = []
        checkpoints = []

        def model(messages, tools, think):
            prompts.append(messages[-1]["content"])
            return {"content": "Complete."}

        task = 'Use packet-text.md. Find "Next class:" to build the page and save it to a new checklist.txt.'
        work = Agent(model, self.emit, lambda: None, lambda *_: False, workspace=self.workspace, mode="read", workflow="site")
        work.run("System", [], task, save_checkpoint=lambda **data: checkpoints.append(data))
        self.assertIn("Workspace source map", prompts[0])
        self.assertIn("p. 6, line 2: Next class: Monday", prompts[0])
        self.assertIn("Full brief\n1: ## PDF page 6", prompts[0])
        self.assertEqual(self.events[0][1]["name"], "map_document")
        self.assertIn("site_source_map", checkpoints[0])

    def test_repeated_unproductive_steps_pause_without_claiming_a_file(self):
        (self.path / "notes.md").write_text("A note.\n")
        calls = []

        def model(messages, tools, think):
            calls.append(messages[-1])
            return {"tool_calls": [tool("read_file", path="notes.md")]}

        work = Agent(model, self.emit, lambda: None, lambda *_: False, workspace=self.workspace, mode="auto")
        with self.assertRaisesRegex(WorkPaused, "four steps without a new result, including one recovery attempt"):
            work.run("System", [], "Make a file")
        self.assertEqual(len(calls), 5)
        self.assertIn("Recovery check", calls[3]["content"], "one recovery step comes before the pause")
        self.assertEqual(work.saved_files, [])

    def test_site_pauses_after_five_steps_without_a_page(self):
        calls = []

        def model(messages, tools, think):
            calls.append(messages)
            return {"tool_calls": [tool("search_text", query=f"phrase {len(calls)}")]}

        work = Agent(model, self.emit, lambda: None, lambda *_: False, workspace=self.workspace, mode="auto",
                     workflow="site")
        with self.assertRaisesRegex(WorkPaused, "five steps without saving an HTML page"):
            work.run("System", [], "Build the page")
        self.assertEqual(len(calls), 5)
        self.assertTrue(any("small working HTML page" in str(messages) for messages in calls[3:]))

    def test_file_edit_keeps_unrelated_reads_and_refreshes_dependent_checks(self):
        (self.path / "brief.md").write_text("Tuesday lab.\n")
        (self.path / "index.html").write_text('<h1>Lab</h1><a href="details.md">Details</a>')
        source = tool("read_file", path="./brief.md")
        output = tool("read_file", path="index.html")
        check = tool("check_page", path="index.html")
        replies = iter([
            {"tool_calls": [source, output, check]},
            {"tool_calls": [tool("write_file", path="details.md", content="A lab guide.\n")]},
            {"tool_calls": [source, check]},
            {"tool_calls": [tool("write_file", path="./index.html", content="<h1>New title</h1>"), output]},
            {"content": "Created the guide."},
        ])
        work = Agent(lambda *_: next(replies), self.emit, lambda: None, lambda *_: False, workspace=self.workspace,
                     mode="auto", web=False, memory=False, workflow="site", num_ctx=16384)
        work.run("System", [], "Build a guide")
        previews = [data["preview"] for event, data in self.events if event == "tool_result"]
        self.assertEqual(sum("Tuesday lab" in preview for preview in previews), 1)
        self.assertTrue(previews[4].startswith("This exact call's result"))
        self.assertIn("Unresolved local link", previews[2])
        self.assertNotIn("Unresolved local link", previews[5])
        self.assertIn("New title", previews[7])


if __name__ == "__main__":
    unittest.main()
