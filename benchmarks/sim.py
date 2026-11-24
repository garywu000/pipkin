"""Simulate a user giving Pipkin Work everyday tasks with a live Ollama model, logging every event.

Usage: python3 benchmarks/sim.py <model> <output-dir> [task ...]   (approves every edit and command)
"""
import json
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402
from run import post, request, server  # noqa: E402

NOTES = """# Bio 101 — Cell Respiration notes

- Glycolysis happens in the cytoplasm. 1 glucose -> 2 pyruvate. Net 2 ATP, 2 NADH.
- Pyruvate oxidation: in mitochondrial matrix, pyruvate -> acetyl-CoA, releases CO2.
- Krebs cycle (citric acid cycle): matrix. Per glucose: 2 ATP, 6 NADH, 2 FADH2, 4 CO2.
- Electron transport chain: inner mitochondrial membrane. O2 is final electron acceptor -> water.
- Chemiosmosis: ATP synthase uses H+ gradient. ~26-28 ATP.
- Total about 30-32 ATP per glucose.
- Fermentation (no O2): lactic acid (muscles) or alcoholic (yeast) regenerates NAD+.
Test is Friday Dec 4!
"""

CALC = """def average(values):
    return sum(values) / len(values) + 1


def grade(score):
    if score > 90:
        return "A"
    if score > 80:
        return "B"
    return "C"
"""
TEST_CALC = """import unittest
from calc import average, grade


class CalcTests(unittest.TestCase):
    def test_average(self):
        self.assertEqual(average([2, 4, 6]), 4)

    def test_grade_boundary(self):
        self.assertEqual(grade(90), "A")
        self.assertEqual(grade(80), "B")


if __name__ == "__main__":
    unittest.main()
"""

ESSAY = "\n\n".join(
    f"Paragraph {i}. " + ("The Industrial Revolution reshaped labor, cities, and energy use. " * 12)
    + (f"A key point in this paragraph: factory towns grew {i * 3}% in the decade studied." if i % 7 == 0 else "")
    for i in range(1, 70)
)

TASKS = {
    "study-guide": {
        "files": {"notes.md": NOTES},
        "task": "Make me a study guide called study-guide.md from my notes: key terms with definitions, "
                "a summary table of where each stage happens and what it makes, and 5 practice questions with answers.",
    },
    "fix-tests": {
        "files": {"calc.py": CALC, "test_calc.py": TEST_CALC},
        "task": "My tests are failing. Fix calc.py (not the tests) and run the tests to prove they pass.",
        "shell": True,
    },
    "two-page-site": {
        "files": {},
        "task": "Make a simple website for my robotics club (Team Sprocket, meets Tuesdays 4pm in Room 112): "
                "index.html and members.html that link to each other and share one style.css.",
    },
    "long-essay": {
        "files": {"essay.txt": ESSAY},
        "task": "Summarize essay.txt into summary.md with 5 bullet points, and include every percentage it mentions.",
    },
    "vague": {
        "files": {"draft1.txt": "Dear Ms. Park, I wanted to ask about the lab.", "draft2.txt": "Dear Ms. Park, I wanted to ask about the lab report due date.",
                  "todo.txt": "- email ms park\n- finish lab\n"},
        "task": "clean this up",
    },
}


def simulate(name, spec, model, context, think):
    started = time.monotonic()
    log = []
    with TemporaryDirectory(prefix="pipkin-sim-") as directory, ExitStack() as stack:
        root = Path(directory)
        work = root / "workspace"
        work.mkdir()
        for path, text in spec["files"].items():
            (work / path).write_bytes(text if isinstance(text, bytes) else text.encode())
        sessions = server.BrowserSessions()
        for attr, value in {"CURRENT": {"model": model, "num_ctx": context}, "SETTINGS_FILE": root / "settings.json",
                            "SESSIONS": sessions, "WORKSPACES": {}}.items():
            stack.enter_context(patch.object(server, attr, value))
        stack.callback(lambda: sessions.unload(timeout=15))
        task = spec["task"]
        if spec.get("attachment"):
            import base64
            doc = post("/api/document", {"kind": "pdf", "data": base64.b64encode(spec["files"][spec["attachment"]]).decode()})
            task += f'\n\nAttached document "{spec["attachment"]}"\n\n[A1] Extracted lines 1–{len(doc["text"].splitlines())}\n{doc["text"]}'
        key = server.grant_workspace(server.Workspace(work))
        options = {"task": task, "workflow": "site" if spec.get("site") else None, "workspace": key, "mode": "ask", "web": False, "allowMemory": False,
                   "think": think, "maxSteps": spec.get("maxSteps", 24), "allowShell": bool(spec.get("shell")), "checkpoint": str(uuid4())}
        try:
            with request("/api/agent/run", options, timeout=300) as response:
                run_id = None
                for line in response:
                    event = json.loads(line)
                    kind = event.get("event")
                    t = round(time.monotonic() - started, 1)
                    if kind == "run":
                        run_id = event["id"]
                    if kind in ("token", "thinking", "delta", "usage", "status", "progress"):
                        continue
                    slim = {k: (v[:400] if isinstance(v, str) else v) for k, v in event.items() if k not in ("diff",)}
                    log.append({"t": t, **slim})
                    if kind == "approval":
                        post("/api/agent/approve", {"run": run_id, "id": event["id"], "approved": True})
        except Exception as error:  # noqa: BLE001
            log.append({"t": round(time.monotonic() - started, 1), "event": "DRIVER_ERROR", "error": repr(error)})
        files = {p.relative_to(work).as_posix(): p.read_text(errors="replace")
                 for p in sorted(work.rglob("*")) if p.is_file()}
    return {"name": name, "seconds": round(time.monotonic() - started, 1), "events": log, "files": files}


from cases import CASES  # noqa: E402
for _name, _case in CASES.items():
    TASKS["bench-" + _name] = {"files": _case.files, "task": _case.task, "site": _case.site,
                               "attachment": _case.attachment, "maxSteps": 10}


if __name__ == "__main__":
    model, out = sys.argv[1], Path(sys.argv[2])
    names = sys.argv[3:] or list(TASKS)
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        think = TASKS[name].get("maxSteps") != 10
        result = simulate(name, TASKS[name], model, 16384, think=think)
        (out / f"{name}.json").write_text(json.dumps(result, indent=1))
        kinds = [e["event"] for e in result["events"]]
        print(name, result["seconds"], "s", {k: kinds.count(k) for k in set(kinds)}, list(result["files"]), flush=True)
