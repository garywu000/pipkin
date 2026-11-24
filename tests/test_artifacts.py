from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from agent import Agent, ApprovalCancelled, WorkPaused, Workspace
import artifacts


def plan(path):
    return {
        "route": "render", "path": path, "title": "Circuit Workshop", "intro": "A short workshop plan.",
        "sections": [{"heading": "Prepare", "items": ["Bring a notebook & pencil."], "sources": []}],
    }


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="pipkin-export-test-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.workspace = Workspace(folder.name)
        self.events = []

    def agent(self, approve, mode="ask", workflow="word"):
        return Agent(None, lambda event, **data: self.events.append((event, data)), lambda: None, approve,
                     workspace=self.workspace, mode=mode, workflow=workflow)

    def run_export(self, agent, request, saved=None, checkpoint=None):
        return agent.run_artifact(
            {"brief": "Write agenda.docx", "attachments": []}, "", saved or {}, request, lambda *_: "",
            stop_requested=lambda: False, take_steering=tuple, save_checkpoint=checkpoint,
        )

    def test_pdf_source_pages_and_attachment_mismatch(self):
        text = "## PDF page 1\nAgenda\n\n## PDF page 3\nPreparation"
        inputs = {"brief": "Build a site from packet.pdf", "attachments": []}
        sources, omitted = artifacts.source_catalog(inputs, lambda _: text, 100)
        self.assertFalse(omitted)
        self.assertEqual([(item["page"], item["path"]) for item in sources], [(1, "packet.pdf"), (3, "packet.pdf")])
        inputs["attachments"] = [{"name": "packet.pdf", "text": "Different packet", "truncated": False}]
        sources, _ = artifacts.source_catalog(inputs, lambda _: text, 100)
        self.assertIsNone(sources[0]["path"])
        self.assertEqual(sources[0]["excerpt"], "Different packet")

    def test_rejects_fabricated_citations(self):
        proposed = plan("index.html")
        proposed["sections"][0]["sources"] = ["S2"]
        with self.assertRaisesRegex(ValueError, "source IDs"):
            artifacts.checked_plan(proposed, "site", [{"id": "S1"}])

    def test_source_filenames_and_requested_output_are_distinct(self):
        brief = 'Create a Word document from notes.docx named "Meeting Notes.docx".'
        self.assertEqual(artifacts.mentioned_paths(brief), ["notes.docx", "Meeting Notes.docx"])
        self.assertEqual(artifacts.destination_paths(brief, "word"), ["Meeting Notes.docx"])
        self.assertEqual(artifacts.destination_paths("Create a PDF from packet.pdf.", "pdf"), [])

    def test_short_handouts_cannot_attribute_invented_items_to_a_source(self):
        sources = [{"id": "S1", "excerpt": "Bring a notebook & pencil."}]
        proposed = plan("agenda.pdf")
        proposed["sections"][0]["sources"] = ["S1"]
        self.assertTrue(artifacts.use_source_lines("pdf", "Create a PDF from notes.md", sources))
        self.assertTrue(artifacts.checked_plan(proposed, "pdf", sources, copy_lines=True))
        proposed["sections"][0]["items"].append("Review the circuit diagrams.")
        with self.assertRaisesRegex(ValueError, "exact text"):
            artifacts.checked_plan(proposed, "pdf", sources, copy_lines=True)

    def test_office_outputs_contain_editable_text_and_valid_parts(self):
        for workflow, name, part in [
            ("word", "agenda.docx", "word/document.xml"),
            ("slides", "agenda.pptx", "ppt/slides/slide2.xml"),
        ]:
            with self.subTest(workflow=workflow):
                data = artifacts.render(workflow, plan(name), {})
                with ZipFile(BytesIO(data)) as package:
                    self.assertIsNone(package.testzip())
                    self.assertIn(b"Bring a notebook &amp; pencil.", package.read(part))
                    self.assertIn("[Content_Types].xml", package.namelist())
                    self.assertFalse(any("macro" in name.lower() for name in package.namelist()))

    def test_decline_and_resume_use_fresh_approval_and_reuse_the_plan(self):
        def interrupt(*_):
            raise ApprovalCancelled()

        saved = {}
        with self.assertRaisesRegex(WorkPaused, "ready for review"):
            self.run_export(self.agent(interrupt), lambda *_: {"content": json.dumps(plan("agenda.docx"))},
                            checkpoint=lambda **fields: saved.update(fields))
        self.assertFalse((self.root / "agenda.docx").exists())

        def review(kind, title, detail):
            self.assertEqual((kind, title), ("edit", "Create agenda.docx"))
            self.assertIn("Bring a notebook", detail)
            return True

        def unexpected_plan(*_):
            self.fail("Resume should reuse the reviewed draft.")

        self.run_export(self.agent(review), unexpected_plan, saved=saved, checkpoint=lambda **fields: saved.update(fields))
        self.assertTrue((self.root / "agenda.docx").is_file())
        self.assertEqual(saved["files"], ["agenda.docx"])
        self.assertTrue(any(data.get("saved_file") == "agenda.docx" for event, data in self.events if event == "tool_result"))
        results = [data for event, data in self.events if event == "output"]
        self.assertEqual([(item["path"], item["action"], item["checks"][0]["kind"], item["checks"][0]["ok"]) for item in results],
                         [("agenda.docx", "created", "package", True)])
        self.assertEqual(saved["outputs"], results)

        self.run_export(self.agent(lambda *_: False), lambda *_: {"content": json.dumps(plan("decline.docx"))},
                        saved={"artifact_draft": {"plan": plan("decline.docx"), "sources": []}})
        self.assertFalse((self.root / "decline.docx").exists())

    def test_a_new_export_does_not_replace_existing_files_or_links(self):
        path = self.root / "report.pdf"
        self.workspace.write_new_artifact(path.name, b"original")
        with self.assertRaisesRegex(ValueError, "exists"):
            self.workspace.write_new_artifact(path.name, b"new")
        expected = ["report.pdf"]
        try:
            (self.root / "alias.pdf").symlink_to(path)
        except OSError:
            pass
        else:
            expected.insert(0, "alias.pdf")
            with self.assertRaisesRegex(ValueError, "exists"):
                self.workspace.write_new_artifact("alias.pdf", b"new")
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(sorted(file.name for file in self.root.iterdir()), expected)


class StagedChecklistTests(unittest.TestCase):
    """The staged pipeline shows no checklist until the model has planned, then one written from that plan."""

    setUp = ArtifactTests.setUp
    run_export = ArtifactTests.run_export

    def agent(self, approve):
        # Copy each event as the server's JSON stream would, since plan steps are updated in place.
        return Agent(None, lambda event, **data: self.events.append((event, json.loads(json.dumps(data)))),
                     lambda: None, approve, workspace=self.workspace, mode="ask", workflow="word")

    def plans(self):
        return [data["steps"] for event, data in self.events if event == "plan"]

    def test_checklist_follows_the_model_plan_and_approval(self):
        order = []

        def request(*_):
            order.append(("model", len(self.plans())))
            return {"content": json.dumps(plan("agenda.docx"))}

        self.run_export(self.agent(lambda *_: True), request)
        self.assertEqual(order, [("model", 0)], "no stand-in checklist before the model plans")
        first, *_, last = self.plans()
        self.assertEqual([step["text"] for step in first],
                         ["Read the brief", "Plan 1 section: Prepare", "Build agenda.docx and check the Word file",
                          "Save agenda.docx after your approval"])
        self.assertEqual([step["status"] for step in first], ["completed", "completed", "in_progress", "pending"])
        self.assertEqual([step["status"] for step in last], ["completed"] * 4)

    def test_declined_output_leaves_the_save_step_open(self):
        self.run_export(self.agent(lambda *_: False), lambda *_: {"content": json.dumps(plan("agenda.docx"))})
        self.assertEqual(self.plans()[-1][-1], {"text": "Save agenda.docx after your approval", "status": "pending"})
        self.assertFalse((self.root / "agenda.docx").exists())

    def test_study_guide_and_checklist_require_per_section_page_citations_and_render_toc(self):
        sources = [
            {"id": "S1", "name": "syllabus.pdf", "page": 2, "path": "syllabus.pdf", "href": "syllabus.pdf#page=2", "excerpt": "Midterm is Oct 15."},
            {"id": "S2", "name": "syllabus.pdf", "page": 5, "path": "syllabus.pdf", "href": "syllabus.pdf#page=5", "excerpt": "Final project due Dec 1."},
        ]
        by_id = {s["id"]: s for s in sources}
        missing_cite = {
            "route": "render", "path": "guide.html", "title": "Course Study Guide", "intro": "Key dates.",
            "sections": [
                {"heading": "Exams", "items": ["Midterm is Oct 15."], "sources": ["S1"]},
                {"heading": "Project", "items": ["Final project due Dec 1."], "sources": []},
            ],
        }
        with self.assertRaisesRegex(ValueError, "Every section"):
            artifacts.checked_plan(missing_cite, "study-guide", sources)

        valid_checklist = {
            "route": "render", "path": "tasks.html", "title": "Course Checklist", "intro": "All deadlines.",
            "sections": [
                {"heading": "Exams", "items": ["Prepare for Oct 15 midterm"], "sources": ["S1"]},
                {"heading": "Project", "items": ["Submit final project by Dec 1"], "sources": ["S2"]},
            ],
        }
        checked = artifacts.checked_plan(valid_checklist, "checklist", sources)
        html = artifacts.render("checklist", checked, by_id).decode()
        self.assertIn('<nav class="toc"', html)
        self.assertIn('type="checkbox"', html)
        self.assertIn('href="syllabus.pdf#page=2"', html)
        self.assertIn('href="syllabus.pdf#page=5"', html)
        self.assertIn("Referenced sources", html)
