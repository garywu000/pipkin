"""Regressions found by running live Qwen 8B tasks through Work."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import agent
from agent import Agent, Workspace

ROOT = Path(__file__).resolve().parent.parent


def tool(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class ReliabilityTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "project"
        self.root.mkdir()
        self.workspace = Workspace(self.root)

    def run_agent(self, replies, task="Do the task.", workflow=None):
        replies = iter(replies)
        events, seen = [], []

        def model(messages, _tools, _think):
            seen.append(messages)
            return next(replies)

        result = Agent(model, lambda event, **data: events.append((event, data)), lambda: None, lambda *_: True,
                       workspace=self.workspace, mode="ask", web=False, workflow=workflow).run("System", [], task)
        tool_outputs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
        return result, events, tool_outputs

    def test_write_page_cannot_replace_an_existing_page(self):
        original = '<!doctype html><h1>Bike Clinic</h1><a href="note.md">Update</a>'
        (self.root / "index.html").write_text(original)
        self.run_agent([
            {"tool_calls": [tool("write_page", path="index.html", title="New", intro="x",
                                 sections=[{"heading": "a", "text": "b"}])]},
            {"content": "Stopped."},
        ])
        self.assertEqual((self.root / "index.html").read_text(), original)

    def test_write_file_needs_a_read_before_replacing(self):
        (self.root / "notes.txt").write_text("keep me")
        _, _, outputs = self.run_agent([
            {"tool_calls": [tool("write_file", path="notes.txt", content="new")]},
            {"tool_calls": [tool("read_file", path="notes.txt")]},
            {"tool_calls": [tool("write_file", path="notes.txt", content="new")]},
            {"content": "Replaced notes.txt."},
        ])
        self.assertIn("have not read it", outputs[0])
        self.assertEqual((self.root / "notes.txt").read_text(), "new")

    def test_new_files_and_own_pages_can_be_rewritten(self):
        page = dict(title="T", intro="i", sections=[{"heading": "h", "text": "t"}])
        self.run_agent([
            {"tool_calls": [tool("write_page", path="index.html", **page)]},
            {"tool_calls": [tool("write_page", path="index.html", **{**page, "title": "T2"})]},
            {"content": "Built index.html."},
        ])
        self.assertIn("<h1>T2</h1>", (self.root / "index.html").read_text())

    def test_narrated_next_step_gets_one_nudge(self):
        (self.root / "a.txt").write_text("hello")
        _, events, _ = self.run_agent([
            {"content": "I will now read a.txt to check it."},
            {"tool_calls": [tool("read_file", path="a.txt")]},
            {"content": "a.txt says hello."},
        ])
        results = [data["text"] for event, data in events if event == "result"]
        self.assertEqual(results, ["a.txt says hello."])
        self.assertTrue(any(event == "notice" and "next step" in data["text"] for event, data in events))

    def test_polite_closers_are_final(self):
        self.assertFalse(agent.announces_next_step("Saved summary.md. Let me know if you want changes."))
        self.assertTrue(agent.announces_next_step("The PDF is binary. I will now use terminal_read."))

    def test_page_text_keeps_safe_links_only(self):
        html = agent.page_text('[p1](packet.pdf#page=1) <a href="x.html">x</a> [bad](javascript:void) <p>t</p>')
        self.assertIn('<a href="packet.pdf#page=1">p1</a>', html)
        self.assertIn('<a href="x.html">x</a>', html)
        self.assertNotIn('href="javascript', html)
        self.assertNotIn("<p>", html)

    def test_pdf_text_is_readable_through_file_tools(self):
        import server  # noqa: F401  (installs the document reader)
        (self.root / "packet.pdf").write_bytes((ROOT / "benchmarks" / "packet.pdf").read_bytes())
        self.assertIn("Workshop meets Thursday", self.workspace.read_file("packet.pdf", 1, 20))
        self.assertIn("Charge your calculator", self.workspace.search_text("packet.pdf", "calculator", lambda: None))

    def test_false_change_claim_is_corrected(self):
        (self.root / "draft1.txt").write_text("Dear Ms. Park")
        _, events, _ = self.run_agent([
            {"tool_calls": [tool("edit_file", path="draft1.txt", old_text="nope", new_text="x")]},
            {"content": "The file draft1.txt has been renamed to cleaned_draft1.txt."},
        ])
        result = [data["text"] for event, data in events if event == "result"][0]
        self.assertIn("no files were created or changed", result)
        self.assertFalse(agent.claims_change("The file was not created because you declined."))

    def test_edit_file_tolerates_indentation_drift(self):
        (self.root / "calc.py").write_text("def average(values):\n    return sum(values) / len(values) + 1\n")
        self.run_agent([
            {"tool_calls": [tool("read_file", path="calc.py")]},
            {"tool_calls": [tool("edit_file", path="calc.py",
                                 old_text="    def average(values):\n        return sum(values) / len(values) + 1",
                                 new_text="    def average(values):\n        return sum(values) / len(values)")]},
            {"content": "Fixed calc.py."},
        ])
        self.assertEqual((self.root / "calc.py").read_text(), "def average(values):\n    return sum(values) / len(values)\n")

    MEMBERS = ('<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <title>Members</title>\n</head>\n'
               '<body>\n  <ul>\n    <li>Lena (captain)</li>\n    <li>Marcus (mechanical)</li>\n    <li>Priya (electrical)</li>\n'
               '  </ul>\n</body>\n</html>\n')

    def test_edit_with_mismatched_indentation_per_line_still_applies(self):
        # Qwen 27B's two failed attempts in a live run: the snippet's lines were indented unlike the file's.
        for old, new in [("    <title>Members</title>\n  </head>", "    <title>Members</title>\n    <link rel=\"stylesheet\" href=\"style.css\">\n  </head>"),
                         ("    <title>Members</title>\n</head>", "    <title>Members</title>\n    <link rel=\"stylesheet\" href=\"style.css\">\n</head>")]:
            (self.root / "members.html").write_text(self.MEMBERS)
            self.run_agent([
                {"tool_calls": [tool("read_file", path="members.html")]},
                {"tool_calls": [tool("edit_file", path="members.html", old_text=old, new_text=new)]},
                {"content": "Linked the stylesheet."},
            ])
            self.assertEqual((self.root / "members.html").read_text(), self.MEMBERS.replace(
                "  <title>Members</title>\n", '  <title>Members</title>\n  <link rel="stylesheet" href="style.css">\n'))

    def test_rewrite_that_drops_content_is_refused_once(self):
        (self.root / "members.html").write_text(self.MEMBERS)
        rewrite = self.MEMBERS.replace("    <li>Lena (captain)</li>\n    <li>Marcus (mechanical)</li>\n    <li>Priya (electrical)</li>\n", "")
        _, _, outputs = self.run_agent([
            {"tool_calls": [tool("read_file", path="members.html")]},
            {"tool_calls": [tool("write_file", path="members.html", content=rewrite)]},
            {"content": "Done."},
        ])
        self.assertIn("would delete 3 existing lines", outputs[1])
        self.assertIn("Lena (captain)", outputs[1])
        self.assertEqual((self.root / "members.html").read_text(), self.MEMBERS)
        _, _, outputs = self.run_agent([
            {"tool_calls": [tool("read_file", path="members.html")]},
            {"tool_calls": [tool("write_file", path="members.html", content=rewrite)]},
            {"tool_calls": [tool("write_file", path="members.html", content=rewrite)]},
            {"content": "Removed the list as asked."},
        ])
        self.assertEqual((self.root / "members.html").read_text(), rewrite, "a repeated write goes through")

    def test_chat_source_ids_are_not_written_into_files(self):
        self.run_agent([
            {"tool_calls": [tool("write_file", path="summary.md", content="Meets Thursday [S7].\n- [ ] Notebook [S7]\n- [ ] Glasses [S6, S7]\n")]},
            {"content": "Saved summary.md."},
        ], task="Create summary.md.")
        self.assertEqual((self.root / "summary.md").read_text(), "Meets Thursday.\n- [ ] Notebook\n- [ ] Glasses\n")

    def test_read_files_over_limit_reads_first_six_and_names_the_rest(self):
        for n in range(8):
            (self.root / f"f{n}.txt").write_text(f"file {n}")
        _, _, outputs = self.run_agent([
            {"tool_calls": [tool("read_files", files=[{"path": f"f{n}.txt"} for n in range(8)])]},
            {"content": "Read them."},
        ])
        self.assertIn("first 6 of 8", outputs[0])
        self.assertIn("Not read yet: f6.txt, f7.txt", outputs[0])
        self.assertIn("file 5", outputs[0])
        self.assertNotIn("file 6", outputs[0])

    def test_empty_search_in_short_file_returns_the_file(self):
        (self.root / "notes.md").write_text("- Glycolysis happens in the cytoplasm.\n")
        _, _, outputs = self.run_agent([
            {"tool_calls": [tool("search_text", path="notes.md", query="key terms")]},
            {"content": "Done reading."},
        ])
        self.assertIn("Glycolysis happens in the cytoplasm", outputs[0])

    def test_commands_do_not_leave_python_caches(self):
        env = agent.isolated_env("/tmp")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertIn("no:cacheprovider", env["PYTEST_ADDOPTS"])

    def test_missing_deliverable_gets_one_nudge(self):
        (self.root / "notes.md").write_text("Glycolysis")
        _, events, _ = self.run_agent([
            {"tool_calls": [tool("read_file", path="notes.md")]},
            {"content": "I compiled everything into study-guide.md."},
            {"tool_calls": [tool("write_file", path="study-guide.md", content="# Guide")]},
            {"content": "Saved study-guide.md."},
        ], task="Make study-guide.md from notes.md.")
        self.assertTrue((self.root / "study-guide.md").exists())
        self.assertTrue(any(event == "notice" and "does not exist yet" in data["text"] for event, data in events))

    def test_declined_deliverable_is_not_requested_again(self):
        replies = iter([
            {"tool_calls": [tool("write_file", path="new.md", content="x")]},
            {"content": "You declined creating new.md, so I stopped."},
        ])
        events = []
        Agent(lambda *_: next(replies), lambda event, **data: events.append((event, data)), lambda: None,
              lambda *_: False, workspace=self.workspace, mode="ask", web=False).run("System", [], "Create new.md.")
        self.assertEqual([data["text"] for event, data in events if event == "result"],
                         ["You declined creating new.md, so I stopped."])

    def test_final_paragraph_promise_is_detected(self):
        self.assertTrue(agent.announces_next_step(
            "Let's fix grade.\n\nI will update grade. After that, I will run the tests again to verify"))

    def test_refused_blind_write_shows_the_file_so_the_next_edit_works(self):
        (self.root / "calc.py").write_text("def average(v):\n    return sum(v) / len(v) + 1\n")
        _, _, outputs = self.run_agent([
            {"tool_calls": [tool("write_file", path="calc.py", content="hallucinated")]},
            {"tool_calls": [tool("edit_file", path="calc.py", old_text=" + 1", new_text="")]},
            {"content": "Fixed calc.py."},
        ])
        self.assertIn("return sum(v) / len(v) + 1", outputs[0])
        self.assertEqual((self.root / "calc.py").read_text(), "def average(v):\n    return sum(v) / len(v)\n")

    def test_repeated_rewrites_get_a_stop_hint(self):
        replies = [{"tool_calls": [tool("write_file", path="g.md", content=f"v{n}")]} for n in range(4)]
        _, _, outputs = self.run_agent(replies + [{"content": "Saved g.md."}], task="Make g.md.")
        self.assertIn("rewritten g.md 3 times", outputs[-1])


if __name__ == "__main__":
    unittest.main()
