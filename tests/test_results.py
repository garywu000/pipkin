from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent import Agent, Workspace


def tool(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class WorkResultTest(unittest.TestCase):
    """The Work result card records what was saved and which checks ran on the current version."""

    def setUp(self):
        directory = TemporaryDirectory(prefix="pipkin-results-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.workspace = Workspace(directory.name)
        self.events = []

    def emit(self, event, **data):
        self.events.append((event, data))

    def outputs(self):
        return [data for event, data in self.events if event == "output"]

    def test_checks_follow_the_current_version_and_reach_the_checkpoint(self):
        saved = {}
        replies = iter([
            {"tool_calls": [tool("write_file", path="index.html", content='<h1 id="top">Plan</h1><a href="#top">Top</a>')]},
            {"tool_calls": [tool("check_page", path="index.html")]},
            {"tool_calls": [tool("edit_file", path="index.html", old_text='href="#top"', new_text='href="#gone"')]},
            {"content": "Saved index.html."},
        ])
        work = Agent(lambda *_: next(replies), self.emit, lambda: None, lambda *_: True,
                     workspace=self.workspace, mode="ask", web=False, workflow="site")
        work.run("System", [], "Make a page.", save_checkpoint=lambda **fields: saved.update(fields))
        records = self.outputs()
        self.assertEqual(records[0]["action"], "created")
        self.assertTrue(records[1]["checks"][0]["ok"], "the site workflow checks each saved page")
        final = records[-1]
        self.assertEqual(final["action"], "created")
        self.assertFalse(final["checks"][0]["ok"], "the check must describe the edited version, not the earlier one")
        self.assertIn("#gone", final["checks"][0]["text"])
        self.assertEqual(saved["outputs"], [final])

    def test_failed_check_and_declined_write(self):
        (self.path / "old.html").write_text("<p>Old</p>")
        replies = iter([
            {"tool_calls": [tool("write_file", path="page.html", content='<a href="#missing">Go</a>')]},
            {"tool_calls": [tool("check_page", path="page.html"), tool("check_page", path="old.html")]},
            {"tool_calls": [tool("write_file", path="notes.txt", content="Declined.\n")]},
            {"content": "Done."},
        ])
        work = Agent(lambda *_: next(replies), self.emit, lambda: None,
                     lambda kind, title, detail: "notes.txt" not in title, workspace=self.workspace, mode="ask", web=False,
                     workflow="site")
        work.run("System", [], "Make a page.")
        final = self.outputs()[-1]
        self.assertEqual(final["path"], "page.html")
        self.assertFalse(final["checks"][0]["ok"])
        self.assertIn("#missing", final["checks"][0]["text"])
        self.assertEqual({item["path"] for item in self.outputs()}, {"page.html"},
                         "a file Pipkin did not write, or one the user declined, is not a result")
        self.assertFalse((self.path / "notes.txt").exists())

    def test_stop_after_saving_keeps_records_from_the_checkpoint(self):
        record = {"path": "index.html", "action": "created", "sources": ["packet.pdf · page 4"],
                  "checks": [{"kind": "links", "ok": True, "text": "Local links, anchors and IDs passed"}]}
        work = Agent(lambda *_: self.fail("no model call after stop"), self.emit, lambda: None, lambda *_: True,
                     workspace=self.workspace, mode="ask", web=False)
        work.run("System", [], "Make a page.", stop_requested=lambda: True,
                 saved={"files": ["index.html"], "outputs": [record]})
        stopped = [data for event, data in self.events if event == "stopped"]
        self.assertEqual(stopped[-1]["outputs"], [record])
        self.assertEqual(stopped[-1]["files"], ["index.html"])


if __name__ == "__main__":
    unittest.main()
