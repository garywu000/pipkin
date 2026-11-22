import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent import Agent, UndoConflict, UndoStore, Workspace
import artifacts


def tool(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class UndoTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="pipkin-undo-")
        store = TemporaryDirectory(prefix="pipkin-undo-store-")
        self.addCleanup(folder.cleanup)
        self.addCleanup(store.cleanup)
        self.root = Path(folder.name)
        self.workspace = Workspace(folder.name)
        self.store = UndoStore(store.name, max_records=5)
        self.events = []

    def run_agent(self, replies, approve=lambda *_: True, **options):
        replies = iter(replies)
        work = Agent(lambda *_: next(replies), lambda event, **data: self.events.append((event, json.loads(json.dumps(data)))),
                     lambda: None, approve, workspace=self.workspace, mode="ask", web=False, undo=self.store, **options)
        work.run("System", [], "Edit the notes.")
        return work

    def undo_id(self, path):
        return [data for event, data in self.events if event == "output" and data["path"] == path][-1]["undo"]

    def test_edit_twice_then_undo_restores_the_original(self):
        (self.root / "notes.md").write_text("Original\n")
        self.run_agent([
            {"tool_calls": [tool("edit_file", path="notes.md", old_text="Original", new_text="First edit")]},
            {"tool_calls": [tool("edit_file", path="notes.md", old_text="First edit", new_text="Second edit")]},
            {"content": "Done."},
        ])
        ids = {data["undo"] for event, data in self.events if event == "output"}
        self.assertEqual(len(ids), 1, "one undo record per file per run")
        self.assertEqual(self.store.apply(self.workspace, ids.pop()), {"action": "restored", "path": "notes.md"})
        self.assertEqual((self.root / "notes.md").read_text(), "Original\n")

    def test_created_file_is_removed_only_while_unchanged(self):
        self.run_agent([{"tool_calls": [tool("write_file", path="new.txt", content="Pipkin wrote this.\n")]},
                        {"content": "Done."}])
        undo_id = self.undo_id("new.txt")
        (self.root / "new.txt").write_text("Pipkin wrote this.\nThe user added a line.\n")
        with self.assertRaisesRegex(UndoConflict, "changed after Pipkin saved it"):
            self.store.apply(self.workspace, undo_id)
        self.assertIn("The user added a line", (self.root / "new.txt").read_text(), "the user's work is kept")
        (self.root / "new.txt").write_text("Pipkin wrote this.\n")
        self.assertEqual(self.store.apply(self.workspace, undo_id)["action"], "removed")
        self.assertFalse((self.root / "new.txt").exists())
        with self.assertRaisesRegex(UndoConflict, "already undone"):
            self.store.apply(self.workspace, undo_id)

    def test_deleted_edit_and_declined_write(self):
        (self.root / "plan.md").write_text("Plan\n")
        self.run_agent([{"tool_calls": [tool("edit_file", path="plan.md", old_text="Plan", new_text="Plan v2")]},
                        {"tool_calls": [tool("write_file", path="skip.txt", content="no")]},
                        {"content": "Done."}],
                       approve=lambda kind, title, detail: "skip.txt" not in title)
        self.assertNotIn("skip.txt", {data["path"] for event, data in self.events if event == "output"})
        (self.root / "plan.md").unlink()
        with self.assertRaisesRegex(UndoConflict, "deleted after Pipkin edited it"):
            self.store.apply(self.workspace, self.undo_id("plan.md"))

    def test_other_folder_is_refused_and_history_is_bounded(self):
        undo_id = self.store.record(self.workspace, "a.txt", None, b"x")
        other = TemporaryDirectory(prefix="pipkin-undo-other-")
        self.addCleanup(other.cleanup)
        with self.assertRaisesRegex(ValueError, "different folder"):
            self.store.apply(Workspace(other.name), undo_id)
        for index in range(8):
            self.store.record(self.workspace, f"f{index}.txt", None, b"x")
            os.utime(self.store.folder / (self.store.record(self.workspace, "g.txt", None, b"y") + ".json"))
        self.assertLessEqual(len(list(self.store.folder.glob("*.json"))), 5)
        with self.assertRaisesRegex(ValueError, "no longer available|Unknown"):
            self.store.apply(self.workspace, "../../etc/passwd")

    def test_export_can_be_removed(self):
        plan = {"route": "render", "path": "agenda.docx", "title": "Workshop", "intro": "",
                "sections": [{"heading": "Prepare", "items": ["Bring a notebook."], "sources": []}]}
        work = Agent(None, lambda event, **data: self.events.append((event, json.loads(json.dumps(data)))), lambda: None,
                     lambda *_: True, workspace=self.workspace, mode="ask", workflow="word", undo=self.store)
        work.run_artifact({"brief": "Write agenda.docx", "attachments": []}, "", {}, lambda *_: {"content": json.dumps(plan)},
                          lambda *_: "", stop_requested=lambda: False, take_steering=tuple, save_checkpoint=None)
        self.assertTrue((self.root / "agenda.docx").exists())
        self.assertEqual(self.store.apply(self.workspace, self.undo_id("agenda.docx"))["action"], "removed")
        self.assertFalse((self.root / "agenda.docx").exists())


if __name__ == "__main__":
    unittest.main()
