"""Synthetic Work tasks with checks on the files left in the workspace."""

import ast
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.links = []
        self.references = []
        self.tags = set()
        self.in_code = False

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag in ("script", "style"):
            self.in_code = True
        for key, value in attrs:
            if key in ("href", "src") and value:
                self.references.append(value)
                if tag == "a" and key == "href":
                    self.links.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.in_code = False

    def handle_data(self, text):
        if not self.in_code:
            self.text.append(text)


def normalized(text):
    text = " ".join(text.casefold().split())
    return re.sub(r"\b(\d{1,2})(?::00)?\s*([ap])\.?\s*m\.?(?!\w)", r"\1 \2m", text)


@dataclass(frozen=True)
class Output:
    example: str
    text: tuple = ()
    links: tuple = ()
    exact: bool = False

    def passes(self, workspace, path):
        return all(self.checks(workspace, path).values())

    def checks(self, workspace, path):
        try:
            content, truncated = workspace.raw_text(path)
            if truncated:
                return {"readable_output": False}
            if self.exact:
                if path.endswith(".py"):
                    return {"expected_syntax_tree": ast.dump(ast.parse(content)) == ast.dump(ast.parse(self.example))}
                return {"expected_lines": content.strip().splitlines() == self.example.strip().splitlines()}
            page = Page()
            page.feed(content)
            page.close()
            visible = normalized(" ".join(page.text))
            references = [urlsplit(ref) for ref in page.references]
            links = {unquote(link).removeprefix("./") for link in page.links}
            return {
                "html_and_heading": {"html", "h1"}.issubset(page.tags),
                "required_facts": all(normalized(phrase) in visible for phrase in self.text),
                "required_source_links": set(self.links).issubset(links),
                "local_references_only": all(not (ref.scheme or ref.netloc) for ref in references),
                "script_free": "script" not in page.tags,
                "static_links_resolve": workspace.check_page(path, lambda: None).splitlines()[-1]
                == "No missing local assets, page anchors, or duplicate IDs found.",
            }
        except (OSError, SyntaxError, ValueError):
            return {"readable_output": False}


@dataclass(frozen=True)
class Case:
    task: str
    files: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    site: bool = False
    attachment: str = ""
    pause_after: str = ""
    decline: str = ""


def page(heading, body):
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><h1>{heading}</h1>{body}</html>\n'


PACKET = Path(__file__).with_name("packet.pdf").read_bytes()
CHECKLIST = "Bring a notebook\nCharge your calculator\n"
BOOKING = "def billed_hours(minutes):\n    return minutes // 60\n"
CASES = {
    "pdf-site": Case(
        "Use the attached packet.pdf to build index.html. Include the Workshop title, meeting time, "
        "and both preparation tasks. Add separate local links to the first and second pages of packet.pdf "
        "using #page=1 and #page=2. Keep the PDF unchanged. Create only index.html, with inline HTML and CSS.",
        {"packet.pdf": PACKET},
        {"index.html": Output(
            page("Circuit Workshop", '<p>Thursday at 4:00 p.m.</p><ul><li>Bring a notebook</li>'
                 '<li>Charge your calculator</li></ul><a href="packet.pdf#page=1">Meeting details</a>'
                 '<a href="packet.pdf#page=2">Preparation</a>'),
            ("Circuit Workshop", "Thursday", "4:00 p.m.", "Bring a notebook", "Charge your calculator"),
            ("packet.pdf#page=1", "packet.pdf#page=2"),
        )},
        site=True, attachment="packet.pdf",
    ),
    "brief-site": Case(
        "Build index.html from brief.md. Show the study session time and both items. Save the two items "
        "as plain lines in checklist.txt and link to it from the page. Also link to brief.md. "
        "Use HTML and CSS only and preserve brief.md.",
        {"brief.md": b"# Study Session\nFriday at 3:30 p.m.\nBring a notebook\nCharge your calculator\n"},
        {"index.html": Output(
            page("Study Session", '<p>Friday at 3:30 p.m.</p><ul><li>Bring a notebook</li>'
                 '<li>Charge your calculator</li></ul><a href="checklist.txt">Checklist</a>'
                 '<a href="brief.md">Source</a>'),
            ("Study Session", "Friday", "3:30 p.m.", "Bring a notebook", "Charge your calculator"),
            ("checklist.txt", "brief.md"),
        ), "checklist.txt": Output(CHECKLIST, exact=True)},
        site=True,
    ),
    "update-site": Case(
        "Update index.html using change-note.md. Change the booking time only. Keep the room "
        "number, existing HTML structure, and the link to the update. Preserve change-note.md.",
        {"change-note.md": b"The Room 204 clinic now starts at 10:30 a.m. Tuesday remains the day.\n",
         "index.html": page("Bike Clinic", '<p>Room 204</p><p>Tuesday at 9:30 a.m.</p>'
                            '<a href="change-note.md">Update</a>').encode()},
        {"index.html": Output(
            page("Bike Clinic", '<p>Room 204</p><p>Tuesday at 10:30 a.m.</p>'
                 '<a href="change-note.md">Update</a>'),
            exact=True,
        )}, site=True,
    ),
    "code-fix": Case(
        "In billing.py, fix billed_hours to round nonnegative whole minutes up to hours. "
        "Use (minutes + 59) // 60 for the return expression. Preserve the function name and billing notes. "
        "Use the file tools to verify the edit.",
        {"billing.py": BOOKING.encode(), "billing-notes.md": b"0 -> 0, 1 -> 1, 60 -> 1, 61 -> 2\n"},
        {"billing.py": Output(BOOKING.replace("minutes // 60", "(minutes + 59) // 60"), exact=True)},
    ),
    "resume-work": Case(
        "In one step, write plan.txt with exactly 'Plan: review notes'. After that save, in a separate "
        "step write checklist.txt with exactly 'Bring a notebook'. Preserve the plan if the run resumes.",
        outputs={"plan.txt": Output("Plan: review notes\n", exact=True),
                 "checklist.txt": Output("Bring a notebook\n", exact=True)},
        pause_after="plan.txt",
    ),
    "denied-write": Case(
        "Create proposal.txt with the text 'New workshop'. If the edit is declined, respect that choice "
        "and give a short final status.",
        {"existing.txt": b"Keep this file.\n"}, decline="proposal.txt",
    ),
}
