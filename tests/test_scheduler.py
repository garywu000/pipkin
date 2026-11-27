"""Scheduled background tasks: schedule math, missed runs, waiting for a busy model, and unattended runs."""
import datetime
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

import scheduler
from scheduler import Scheduler, Store, clean_task, next_run

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

def post(port, path, payload):
    """POST to a real test server the way the page does."""
    import http.client
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        connection.request("POST", path, body=json.dumps(payload).encode(),
                           headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
        response = connection.getresponse()
        body = json.loads(response.read() or b"{}")
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}: {body.get('error')}")
        return body
    finally:
        connection.close()


# Wednesday, November 25, 2026
WED = datetime.datetime(2026, 11, 25, 9, 0)


THU = datetime.datetime(2026, 11, 26, 10, 0)   # Thursday, 10:00
TOOL_ARGS = {"title": "Notes summary", "prompt": "Summarize my notes.", "repeat": "weekly", "days": ["Wed"], "time": "18:00"}


def propose(request, now=THU, **changes):
    return scheduler.proposal_from_tool({**TOOL_ARGS, **changes}, now, request)


class ProposalFromToolTest(unittest.TestCase):
    """The model proposes a task; the user's own words override details small models often get wrong."""

    def check(self, request, schedule, flags=(), **changes):
        result = propose(request, **changes)
        self.assertEqual(result["task"]["schedule"], schedule, request)
        self.assertEqual(result["needsClarification"], list(flags), request)
        self.assertIsNone(result["problem"], request)
        return result

    def test_the_users_words_override_the_models_mistakes(self):
        weekly_6pm = {"kind": "weekly", "time": "18:00", "days": [2]}
        request = "Summarize my notes every Wednesday at 6pm"
        for wrong in ({"time": "06:00"}, {"time": "6:00"}, {"time": "18:00:00"}, {"repeat": "daily"}, {"days": ["Fri"]}):
            with self.subTest(model=wrong):
                self.check(request, weekly_6pm, **wrong)
        self.check("Every Monday and Thursday at 4pm, review flashcards", {"kind": "weekly", "time": "16:00", "days": [0, 3]},
                   days=["Mon"])
        self.check("Next Friday at 5pm, review my essay", {"kind": "once", "time": "17:00", "date": "2026-11-27"}, ["date"],
                   repeat="once", date="2026-11-26")
        self.check("Tomorrow at 9am, make a study plan", {"kind": "once", "time": "09:00", "date": "2026-11-27"},
                   repeat="once", date="2026-11-28")
        self.check("Review tomorrow's agenda on Friday at 6pm", {"kind": "once", "time": "18:00", "date": "2026-11-27"},
                   repeat="once")
        self.check("On Dec 3 at 10am, prepare my slides", {"kind": "once", "time": "10:00", "date": "2026-12-03"},
                   repeat="once", date=None)
        self.check("Monday through Friday at 6:45 am, list today's assignments", {"kind": "weekdays", "time": "06:45"},
                   repeat="weekly")
        self.check("Every weekend at 10am, plan my week", {"kind": "weekly", "time": "10:00", "days": [5, 6]})
        self.check("Check my inbox at 5 every evening", {"kind": "daily", "time": "17:00"}, repeat="daily", time="05:00")

    def test_timing_ranges_and_invalid_explicit_values(self):
        self.check("Summarize Friday's notes every Wednesday at 6pm",
                   {"kind": "weekly", "time": "18:00", "days": [2]})
        self.check("Every Tuesday through Thursday at 9am, review notes",
                   {"kind": "weekly", "time": "09:00", "days": [1, 2, 3]})
        self.check("Every Friday through Monday at 6pm, review notes",
                   {"kind": "weekly", "time": "18:00", "days": [0, 4, 5, 6]})
        self.check("Next weekend at 6pm, review notes",
                   {"kind": "once", "time": "18:00", "date": "2026-12-05"}, ["date"])
        self.check("Every Wednesday, summarize notes",
                   {"kind": "weekly", "time": "18:00", "days": [2]}, ["time"], time="18:00:00")
        for request in ("On Feb 30 at 6pm review notes", "On 2026-13-01 at 9am review notes",
                        "Every day at 25:00 review notes", "Every day at 13pm review notes"):
            with self.subTest(request=request):
                self.assertTrue(propose(request)["problem"])
        for request in ("Can you explain why lab happens every Wednesday?", "I read my notes every Wednesday",
                        "List files created on Friday", "Summarize Friday's notes", "Check the daily log"):
            with self.subTest(request=request):
                self.assertIsNone(scheduler.schedule_hint(request, THU))

    def test_referenced_dates_and_times_do_not_replace_the_schedule(self):
        self.check("Every Wednesday at 6pm summarize the Monday 9am meeting",
                   {"kind": "weekly", "time": "18:00", "days": [2]})
        self.check("Summarize notes from Monday through Friday every Sunday at 6pm",
                   {"kind": "weekly", "time": "18:00", "days": [6]})
        self.check("Every day at 6pm summarize notes from Monday through Friday",
                   {"kind": "daily", "time": "18:00"})
        self.check("Every Wednesday at 6 summarize the morning meeting",
                   {"kind": "weekly", "time": "18:00", "days": [2]}, ["time"])
        self.assertIn("unsupported", propose("Every Wednesday at 6pm and Friday at 9am, summarize notes"))
        for request in ("Show my calendar tomorrow", "List files for Friday", "Summarize the Monday 9am meeting"):
            with self.subTest(request=request):
                self.assertIsNone(scheduler.schedule_hint(request, THU))

    def test_guesses_are_flagged_for_the_card(self):
        self.check("Every Wednesday, summarize my notes", {"kind": "weekly", "time": "09:00", "days": [2]}, ["time"], time=None)
        self.check("Every morning, check my folder", {"kind": "daily", "time": "08:00"}, ["time"], repeat="daily", time=None)
        self.check("Run the tests tonight", {"kind": "once", "time": "20:00", "date": "2026-11-26"}, ["time"], repeat="once",
                   time=None)
        self.check("Do the weekly review every week", {"kind": "weekly", "time": "17:00", "days": [4]}, ["days", "time"],
                   days=["Fri"], time="17:00")
        self.check("Summarize notes every Wednesday at 6", {"kind": "weekly", "time": "18:00", "days": [2]}, ["time"])
        # "at 9am" asked after 9am means tomorrow, and the card says so.
        self.check("At 9am summarize my notes", {"kind": "once", "time": "09:00", "date": "2026-11-27"}, ["kind", "date"],
                   repeat="once", date="2026-11-26")

    def test_ordinary_words_are_not_read_as_days_or_schedules(self):
        self.check("Check the daily log for errors every Monday at 9am", {"kind": "weekly", "time": "09:00", "days": [0]})
        self.check("Sun is out, water the plants every sat at 7am", {"kind": "weekly", "time": "07:00", "days": [5]},
                   days=["Sun"])

    def test_repeats_pipkin_cannot_do_are_reported_not_guessed(self):
        for request in ("Every other Friday, review my budget", "Every hour, check the build",
                        "On the first of every month, archive my notes", "Every day at 9am and 5pm, check my folder",
                        "Twice a week, tidy my notes", "Back up my notes monthly"):
            with self.subTest(request=request):
                self.assertIn("unsupported", propose(request))

    def test_permissions_need_the_users_own_words(self):
        quiet = propose("Summarize notes every Wednesday at 6pm", create_files=True, web=True)["task"]
        self.assertEqual((quiet["access"], quiet["web"]), ("read", False))
        loud = propose("Every Wednesday at 6pm, search the web for news and save a summary file", create_files=True, web=True)["task"]
        self.assertEqual((loud["access"], loud["web"]), ("create", True))
        # Seen with Qwen3 8B: the user asked for a new file but the model left create_files out.
        asked = propose("Every Friday at 5pm, write my weekly review of notes.md to a new file", create_files=False)["task"]
        self.assertEqual(asked["access"], "create")
        refused = propose("Every Wednesday at 6pm, read only: don't create files or use the internet", create_files=True, web=True)["task"]
        self.assertEqual((refused["access"], refused["web"]), ("read", False))
        for field in ("id", "created", "folder", "confirmationId", "skill"):
            self.assertNotIn(field, quiet)

    def test_empty_instructions_fall_back_to_the_request_and_past_times_are_problems(self):
        self.assertEqual(propose("Summarize my notes every Wednesday at 6pm", prompt="")["task"]["prompt"], "Summarize my notes")
        late = propose("Today at 9am, summarize my notes", repeat="once")
        self.assertEqual(late["problem"], "That time has already passed today.")
        self.assertIsNone(late["nextRun"])
        with self.assertRaises(ValueError):
            scheduler.proposal_from_tool({"prompt": "x"}, THU, "Summarize my notes")   # no schedule anywhere


class ScheduleToolTest(unittest.TestCase):
    """The agent can only propose: the task exists after the user confirms the card."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def run_agent(self, decision, *, mode="ask", workspace=True, schedule=True, request="Summarize my notes every Wednesday at 6pm"):
        import agent
        saved, approvals, outputs = [], [], []
        replies = iter([{"tool_calls": [{"function": {"name": "schedule_task", "arguments": dict(TOOL_ARGS, time="06:00")}}]},
                        {"content": "Done."}])

        def model(messages, tools, think, **_options):
            return next(replies, {"content": "Done."})

        def emit(event, **fields):
            if event == "tool_result":
                outputs.append(fields["preview"])

        def approve(kind, title, detail):
            approvals.append((kind, title, detail))
            return decision

        def save(task):
            saved.append(task)
            return {**task, "nextRun": "2026-12-02T18:00:00"}

        runner = agent.Agent(model, emit, lambda: None, approve,
                             workspace=agent.Workspace(self.root) if workspace else None, mode=mode, web=False,
                             memory=False, schedule=save if schedule else None)
        tools = runner.tool_names
        runner.run("system", [], request)
        return tools, approvals, saved, outputs

    def test_confirming_creates_the_task_the_user_saw(self):
        tools, approvals, saved, outputs = self.run_agent(True)
        self.assertIn("schedule_task", tools)
        kind, title, detail = approvals[0]
        self.assertEqual(kind, "schedule")
        self.assertEqual(detail["task"]["schedule"], {"kind": "weekly", "time": "18:00", "days": [2]}, "6pm, not the model's 06:00")
        self.assertEqual(detail["folder"], self.root.name)
        self.assertEqual(saved, [detail["task"]])
        self.assertIn("Scheduled", outputs[0])

    def test_not_now_creates_nothing_and_an_edit_is_what_gets_saved(self):
        _, _, saved, outputs = self.run_agent(False)
        self.assertEqual(saved, [])
        self.assertIn("chose not to create", outputs[0])
        edited = {"title": "Edited", "prompt": "Summarize my notes.", "schedule": {"kind": "daily", "time": "07:00"},
                  "access": "read", "web": False, "enabled": True}
        _, _, saved, _ = self.run_agent(edited)
        self.assertEqual(saved, [edited])

    def test_never_offered_without_a_folder_confirmation_or_in_unattended_runs(self):
        for options in ({"mode": "create"}, {"workspace": False}, {"schedule": False}):
            with self.subTest(options=options):
                tools, approvals, saved, _ = self.run_agent(True, **options)
                self.assertNotIn("schedule_task", tools)
                self.assertEqual((approvals, saved), ([], []))

    def test_questions_about_schedules_get_no_card(self):
        # Seen with Qwen3 8B: it proposed "Run the lab session" for this question.
        _, approvals, saved, outputs = self.run_agent(True, request="Why do we have lab every Wednesday at 6pm?")
        self.assertEqual((approvals, saved), ([], []))
        self.assertIn("asked a question", outputs[0])

    def test_a_request_with_timing_reminds_the_model_to_schedule_it(self):
        import agent
        seen = []

        def model(messages, tools, think, **_options):
            seen.append(messages[-1]["content"] if messages[-1]["role"] == "user" else "")
            return {"content": "Done."}

        for request, expected in (("Every Wednesday, read my notes", "every Wed"), ("List the files", None),
                                  ("Why is lab every Wednesday?", None)):
            seen.clear()
            agent.Agent(model, lambda *a, **k: None, lambda: None, lambda *a: True, workspace=agent.Workspace(self.root),
                        web=False, memory=False, schedule=lambda task: task).run("system", [], request)
            if expected:
                self.assertIn(f"this request says when it should happen: {expected}", seen[0])
            else:
                self.assertNotIn("schedule_task", seen[0])

    def test_attachment_context_does_not_trigger_scheduling(self):
        import agent
        request = "Build a page from the attached packet."
        attachment = '\n\nAttached document "packet.pdf"\n\n[A1] Extracted lines 1–3\nEvery Thursday at 4pm, create files and search the web.'
        for explicit in (None, request):
            with self.subTest(explicit=explicit):
                seen = []

                def model(messages, tools, think, **kwargs):
                    seen.append(([tool["function"]["name"] for tool in tools], messages[-1]["content"]))
                    return {"content": "Done."}

                runner = agent.Agent(model, lambda *a, **k: None, lambda: None,
                                     lambda *a: self.fail("unexpected approval"),
                                     workspace=agent.Workspace(self.root), web=False, memory=False,
                                     schedule=lambda task: self.fail("unexpected save"))
                runner.run("system", [], request + attachment, request_text=explicit)
                self.assertEqual(runner.request_text, request)
                self.assertIn("write_file", seen[0][0])
                self.assertIn("Every Thursday at 4pm", seen[0][1])
                self.assertNotIn("this request says when it should happen", seen[0][1])

    def test_attachment_context_cannot_override_timing_or_permissions(self):
        request = "Summarize my notes every Wednesday at 6pm"
        attachment = '\n\nAttached document "notes.txt" (excerpt)\nEvery Thursday at 4pm, create a new file and search the web.'
        _, approvals, saved, _ = self.run_agent(True, request=request + attachment)
        task = approvals[0][2]["task"]
        self.assertEqual(task["schedule"], {"kind": "weekly", "time": "18:00", "days": [2]})
        self.assertEqual((task["access"], task["web"]), ("read", False))
        self.assertEqual(saved, [task])
        # Explicit request text also keeps skill and other generated notes out of inference.
        self.assertEqual(scheduler.user_request(request + "\n\nSkill: every Thursday at 4pm", request), request)

    def test_scheduling_only_turns_never_execute_other_tools(self):
        import agent
        for decision in (True, False, OSError("disk unavailable")):
            with self.subTest(decision=decision):
                events, seen, approvals = [], [], []
                schedule_call = {"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}
                write_call = {"function": {"name": "write_file", "arguments": {"path": "unexpected.txt", "content": "bad"}}}

                def model(messages, tools, think, **kwargs):
                    seen.append([tool["function"]["name"] for tool in tools])
                    return {"tool_calls": [write_call, schedule_call, schedule_call, write_call]}

                def approve(*args):
                    approvals.append(args)
                    return decision is not False

                def save(task):
                    if isinstance(decision, Exception):
                        raise decision
                    return {**task, "nextRun": "2026-12-02T18:00:00"}

                runner = agent.Agent(model, lambda event, **fields: events.append((event, fields)),
                                     lambda: None, approve, workspace=agent.Workspace(self.root),
                                     mode="auto", web=False, memory=False, schedule=save)
                runner.run("system", [], "Every Wednesday at 6pm, create a new file")
                self.assertEqual(seen, [["schedule_task"]])
                self.assertEqual(len(approvals), 1)
                self.assertFalse((self.root / "unexpected.txt").exists())
                outcome = next(fields for event, fields in events if event == "tool_result")
                expected = "failed" if isinstance(decision, Exception) else "saved" if decision else "declined"
                self.assertEqual(outcome["scheduleStatus"], expected)
                answer = next(fields["text"] for event, fields in events if event == "result")
                self.assertIn("Scheduled" if decision is True else "Not scheduled" if expected == "failed" else "chose not to create", answer)

    def test_schedule_only_model_ignores_tool_twice(self):
        import agent
        events, calls = [], []
        def model(messages, tools, think, **kwargs):
            calls.append([tool["function"]["name"] for tool in tools])
            return {"tool_calls": [{"function": {"name": "write_file", "arguments": {
                "path": "unexpected.txt", "content": "bad"}}}]}
        runner = agent.Agent(model, lambda event, **fields: events.append((event, fields)),
                             lambda: None, lambda *args: self.fail("unexpected approval"),
                             workspace=agent.Workspace(self.root), mode="auto", web=False, memory=False,
                             schedule=lambda task: self.fail("unexpected save"))
        runner.run("system", [], "Every day at 6pm, create unexpected.txt")
        self.assertEqual(calls, [["schedule_task"], ["schedule_task"]])
        self.assertFalse((self.root / "unexpected.txt").exists())
        self.assertIn("No task was scheduled", next(fields["text"] for event, fields in events if event == "result"))

    def test_unsupported_repeats_are_explained_without_a_card(self):
        _, approvals, saved, outputs = self.run_agent(True, request="Summarize my notes every other Wednesday at 6pm")
        self.assertEqual((approvals, saved), ([], []))
        self.assertIn("can't repeat a task every other day or week", outputs[0])


class ScheduleConfirmationEndpointTest(unittest.TestCase):
    """A real Work run: the card's approval can carry the edited task, and only schedule cards accept one."""

    def test_work_run_creates_the_confirmed_task_in_its_folder(self):
        import run as bench
        import server

        def step(_handler, messages, tools, _think, _check, emit, **options):
            emit("step")
            if options.get("output_schema") is not None:
                return {"content": json.dumps({"route": "agent"})}
            if not any(message.get("role") == "tool" for message in messages):
                return {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]}
            return {"content": "It's scheduled."}

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ws").mkdir()
            sessions = server.BrowserSessions()
            with patch.object(server, "CURRENT", {"model": "replay", "num_ctx": 8192}), \
                    patch.object(server, "SETTINGS_FILE", root / "settings.json"), \
                    patch.object(server, "SESSIONS", sessions), patch.object(server, "WORKSPACES", {}), \
                    patch.object(server, "SCHEDULER", None), \
                    patch.object(sessions, "ensure_loaded"), patch.object(server.ChatHandler, "agent_step", step):
                key = server.grant_workspace(server.Workspace(root / "ws"))
                edited = {"title": "Wednesday notes", "prompt": "Summarize my notes.", "access": "read", "web": False,
                          "enabled": True, "schedule": {"kind": "weekly", "days": [2], "time": "18:30"}}
                with bench.request("/api/agent/run", {"task": "Summarize my notes every Wednesday at 6pm", "workspace": key,
                                                      "mode": "auto", "web": False, "allowMemory": False, "think": False,
                                                      "maxSteps": 6}, timeout=30) as response:
                    run_id, kinds = None, []
                    for line in response:
                        event = json.loads(line)
                        kinds.append(event["event"])
                        if event["event"] == "run":
                            run_id = event["id"]
                        if event["event"] == "approval":
                            self.assertEqual(event["kind"], "schedule")
                            self.assertEqual(server.schedule_state()["tasks"], [], "nothing exists before confirming")
                            with self.assertRaises(RuntimeError, msg="an edited task needs approved=true"):
                                bench.post("/api/agent/approve", {"run": run_id, "id": event["id"], "approved": False, "task": edited})
                            bench.post("/api/agent/approve", {"run": run_id, "id": event["id"], "approved": True, "task": edited})
                        if event["event"] == "approval_result":
                            self.assertIs(event["approved"], True)
                self.assertIn("result", kinds)
                tasks = server.schedule_state()["tasks"]
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["title"], "Wednesday notes")
                self.assertEqual(tasks[0]["schedule"]["time"], "18:30")
                self.assertEqual(tasks[0]["folder"], str((root / "ws").resolve()))

    def test_only_schedule_cards_accept_an_edited_task(self):
        import server
        from threading import Condition
        run = {"changed": Condition(), "pending": {"c1"}, "kinds": {"c1": "edit"}, "decisions": {},
               "stop_after_step": Event(), "steering": [], "steering_seen": 0}
        with patch.dict(server.RUNS, {"r1": run}):
            handler = Mock()
            handler.read_json.return_value = {"run": "r1", "id": "c1", "approved": True, "task": {"prompt": "x"}}
            server.ChatHandler.approve_action(handler)
            self.assertEqual(handler.send_json.call_args.args[0], 400)
            self.assertEqual(run["decisions"], {})

    def test_a_retried_confirmation_returns_the_task_it_created(self):
        import server
        from uuid import uuid4
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ws").mkdir()
            with patch.object(server, "SETTINGS_FILE", root / "settings.json"), patch.object(server, "SCHEDULER", None):
                task = propose("Summarize my notes every Wednesday at 6pm", now=datetime.datetime.now())["task"]
                confirmation = str(uuid4())
                first = server.save_scheduled_task(task, str(root / "ws"), confirmation=confirmation)
                again = server.save_scheduled_task(task, None, confirmation=confirmation)   # folder grant expired
                self.assertEqual(first["id"], again["id"])
                self.assertEqual(len(server.schedule_state()["tasks"]), 1)


class NextRunTest(unittest.TestCase):
    def test_daily_runs_later_today_or_tomorrow(self):
        self.assertEqual(next_run({"kind": "daily", "time": "17:00"}, WED), WED.replace(hour=17))
        self.assertEqual(next_run({"kind": "daily", "time": "08:00"}, WED), datetime.datetime(2026, 11, 26, 8, 0))

    def test_weekdays_skip_the_weekend(self):
        friday_evening = datetime.datetime(2026, 11, 27, 20, 0)
        self.assertEqual(next_run({"kind": "weekdays", "time": "07:30"}, friday_evening),
                         datetime.datetime(2026, 11, 30, 7, 30), "Friday evening -> Monday morning")

    def test_weekly_picks_the_next_chosen_day(self):
        self.assertEqual(next_run({"kind": "weekly", "time": "17:00", "days": [4]}, WED),
                         datetime.datetime(2026, 11, 27, 17, 0))
        self.assertEqual(next_run({"kind": "weekly", "time": "08:00", "days": [2]}, WED),
                         datetime.datetime(2026, 12, 2, 8, 0), "same weekday, but the time has passed")

    def test_once_runs_once(self):
        schedule = {"kind": "once", "time": "10:00", "date": "2026-11-25"}
        self.assertEqual(next_run(schedule, WED), WED.replace(hour=10))
        self.assertIsNone(next_run(schedule, WED.replace(hour=11)))


class CleanTaskTest(unittest.TestCase):
    def test_valid_task_gets_an_id_and_a_title(self):
        task = clean_task({"prompt": "Summarize this week's notes into review.md", "access": "create",
                           "schedule": {"kind": "weekly", "time": "17:00", "days": [4, 4]}})
        self.assertTrue(task["id"])
        self.assertTrue(task["title"].startswith("Summarize"))
        self.assertEqual(task["schedule"]["days"], [4])
        self.assertFalse(task["web"])

    def test_task_with_skill_allows_empty_prompt_and_validates_checks(self):
        task = clean_task({
            "prompt": "",
            "skill": {
                "name": "/weekly-review",
                "instructions": "Read notes and write weekly-review.md.",
                "checks": [{"type": "file_exists", "pattern": "*.md"}, {"type": "no_placeholders"}],
            },
            "access": "create",
            "schedule": {"kind": "weekly", "time": "17:00", "days": [4]},
        })
        self.assertEqual(task["title"], "/weekly-review")
        self.assertEqual(task["skill"]["name"], "weekly-review")
        self.assertEqual(len(task["skill"]["checks"]), 2)

    def test_invalid_tasks_are_refused_with_a_reason(self):
        for raw, message in [
            ({"prompt": "", "schedule": {"kind": "daily", "time": "09:00"}}, "Describe"),
            ({"prompt": "x", "schedule": {"kind": "hourly", "time": "09:00"}}, "how often"),
            ({"prompt": "x", "schedule": {"kind": "daily", "time": "25:00"}}, "valid time"),
            ({"prompt": "x", "schedule": {"kind": "weekly", "time": "09:00", "days": []}}, "day of the week"),
            ({"prompt": "x", "schedule": {"kind": "once", "time": "09:00"}}, "date"),
            ({"prompt": "x", "access": "auto", "schedule": {"kind": "daily", "time": "09:00"}}, "may do"),
        ]:
            with self.assertRaisesRegex(ValueError, message):
                clean_task(raw)


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name))
        self.now = WED
        self.runs = []
        self.result = {"status": "complete", "summary": "Made review.md.", "outputs": [{"path": "review.md"}]}
        self.busy = False
        self.scheduler = Scheduler(self.store, self.fake_run, busy=lambda: self.busy, clock=lambda: self.now)

    def fake_run(self, task, stop_event, log=None):
        self.runs.append(task["id"])
        return self.result

    def add(self, next_at, **fields):
        task = clean_task({"prompt": "Check the folder", "schedule": {"kind": "daily", "time": "09:00"}, **fields})
        task.update(folder="/tmp/x", nextRun=scheduler.iso(next_at))
        self.store.update(lambda data: data["tasks"].append(task))
        return task

    def test_a_due_task_runs_and_lands_in_the_inbox(self):
        task = self.add(WED - datetime.timedelta(minutes=1))
        self.scheduler.tick()
        self.assertEqual(self.runs, [task["id"]])
        data = self.store.load()
        self.assertEqual(data["inbox"][0]["status"], "complete")
        self.assertEqual(data["inbox"][0]["summary"], "Made review.md.")
        self.assertFalse(data["inbox"][0]["read"])
        self.assertEqual(data["tasks"][0]["nextRun"], "2026-11-26T09:00:00", "advances to the next occurrence")

    def test_a_run_missed_while_closed_waits_for_the_user(self):
        task = self.add(WED - datetime.timedelta(hours=3))
        self.scheduler.tick()
        self.assertEqual(self.runs, [], "missed work never starts by itself")
        saved = self.store.load()["tasks"][0]
        self.assertEqual(saved["missed"], scheduler.iso(WED - datetime.timedelta(hours=3)))
        self.assertEqual(saved["nextRun"], "2026-11-26T09:00:00")
        self.scheduler.run_now(task["id"])
        self.scheduler.tick()
        self.assertEqual(self.runs, [task["id"]])
        self.assertNotIn("missed", self.store.load()["tasks"][0], "running it clears the missed notice")
        self.assertEqual(self.store.load()["inbox"][0]["trigger"], "manual")

    def test_paused_and_disabled_tasks_do_not_run(self):
        self.add(WED - datetime.timedelta(minutes=1), enabled=False)
        self.scheduler.tick()
        self.store.update(lambda data: data.update(paused=True))
        paused_task = self.add(WED - datetime.timedelta(hours=2))
        self.scheduler.tick()
        self.assertEqual(self.runs, [])
        saved = scheduler.find(self.store.load()["tasks"], paused_task["id"])
        self.assertNotIn("missed", saved, "pausing should not falsely mark tasks as missed while closed")
        self.assertEqual(saved["nextRun"], "2026-11-26T09:00:00")

    def test_a_busy_model_delays_the_run_then_skips_it(self):
        task = self.add(WED - datetime.timedelta(minutes=1))
        self.busy = True
        self.scheduler.tick()
        self.assertEqual(self.runs, [])
        self.assertEqual(self.store.load()["inbox"], [], "waiting is not an outcome yet")
        self.now = WED + datetime.timedelta(minutes=20)
        self.busy = False
        self.scheduler.tick()
        self.assertEqual(self.runs, [task["id"]], "runs once the model is free, even past the missed grace period")
        self.runs.clear()
        self.add(self.now - datetime.timedelta(minutes=1))
        self.busy = True
        self.scheduler.tick()
        self.now += datetime.timedelta(minutes=50)
        self.scheduler.tick()
        item = self.store.load()["inbox"][0]
        self.assertEqual(item["status"], "skipped")
        self.assertIn("model was busy", item["summary"])

    def test_the_server_reporting_busy_counts_as_waiting(self):
        self.add(WED - datetime.timedelta(minutes=1))
        self.result = {"status": "busy"}
        self.scheduler.tick()
        self.assertEqual(self.store.load()["inbox"], [])
        self.assertIn("waitReason", self.store.load()["tasks"][0])

    def test_one_time_tasks_turn_off_after_running(self):
        task = clean_task({"prompt": "Once", "schedule": {"kind": "once", "time": "08:59", "date": "2026-11-25"}})
        task.update(folder="/tmp/x", nextRun="2026-11-25T08:59:00")
        self.store.update(lambda data: data["tasks"].append(task))
        self.scheduler.tick()
        saved = self.store.load()["tasks"][0]
        self.assertFalse(saved["enabled"])
        self.assertIsNone(saved["nextRun"])

    def test_a_crashing_run_is_recorded_and_the_scheduler_continues(self):
        self.add(WED - datetime.timedelta(minutes=1))
        self.scheduler.run_task = lambda task, stop, log=None: 1 / 0
        self.scheduler.tick()
        self.assertEqual(self.store.load()["inbox"][0]["status"], "error")
        self.assertIsNone(self.scheduler.running)


class ProcessLockTest(unittest.TestCase):
    def test_only_one_holder(self):
        with TemporaryDirectory() as directory:
            first = scheduler.ProcessLock(Path(directory) / "scheduler.lock")
            second = scheduler.ProcessLock(Path(directory) / "scheduler.lock")
            self.assertTrue(first.acquire())
            if sys.platform != "win32":
                # flock locks are per open file description, so a second handle in the same process is refused too.
                self.assertFalse(second.acquire())
            first.handle.close()


class UnattendedRunTest(unittest.TestCase):
    """A scheduled run goes through the real Work endpoint in new-files-only mode, and the inbox can undo it."""

    def test_read_only_scheduled_runs_cannot_propose_another_schedule(self):
        import server
        import run as bench
        seen = []

        def step(_handler, messages, tools, _think, _check, emit, **options):
            seen.append({tool["function"]["name"] for tool in tools})
            return {"content": "The notes are summarized."}

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(server, "CURRENT", {"model": "replay", "num_ctx": 8192}), \
                    patch.object(server, "SETTINGS_FILE", root / "settings.json"), \
                    patch.object(server, "WORKSPACES", {}), \
                    patch.object(server.SESSIONS, "ensure_loaded"), patch.object(server.ChatHandler, "agent_step", step):
                key = server.grant_workspace(server.Workspace(root))
                with bench.request("/api/agent/run", {"task": "Every Wednesday, summarize notes",
                                                     "workspace": key, "mode": "read", "scheduledRun": True,
                                                     "web": False, "allowMemory": False}) as response:
                    events = [json.loads(line) for line in response]
                self.assertTrue(seen)
                self.assertNotIn("schedule_task", set().union(*seen))
                self.assertNotIn("approval", [event["event"] for event in events])
                self.assertIn("result", [event["event"] for event in events])

    def test_create_mode_makes_new_files_refuses_to_change_old_ones_and_undoes(self):
        import server
        replies = iter([
            {"tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "notes.md", "content": "replaced\n"}}}]},
            {"tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "review-2026-11-25.md",
                                                                              "content": "# Review\n- item\n"}}}]},
            {"content": "Made review-2026-11-25.md from your notes."},
        ])
        seen_tools = []

        def step(_handler, messages, tools, _think, _check, emit, **options):
            emit("step")
            if options.get("output_schema") is not None:
                return {"content": json.dumps({"route": "agent"})}
            seen_tools.append({tool["function"]["name"] for tool in tools})
            return next(replies, {"content": "Done."})

        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "ws"
            folder.mkdir()
            (folder / "notes.md").write_text("original notes\n")
            sessions = server.BrowserSessions()
            with patch.object(server, "CURRENT", {"model": "replay", "num_ctx": 8192}), \
                    patch.object(server, "SETTINGS_FILE", root / "settings.json"), \
                    patch.object(server, "SESSIONS", sessions), patch.object(server, "WORKSPACES", {}), \
                    patch.object(sessions, "ensure_loaded"), patch.object(server.ChatHandler, "agent_step", step):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ChatHandler)
                Thread(target=httpd.serve_forever, daemon=True).start()
                self.addCleanup(httpd.server_close)
                self.addCleanup(httpd.shutdown)
                port = httpd.server_address[1]
                task = {"id": "t1", "title": "Weekly review", "prompt": "Turn notes.md into a review sheet.",
                        "folder": str(folder), "access": "create", "web": False}
                result = server.run_scheduled_task(task, Event(), port)
                self.assertEqual(result["status"], "complete", result)
                self.assertEqual((folder / "notes.md").read_text(), "original notes\n", "existing files are never changed")
                self.assertEqual((folder / "review-2026-11-25.md").read_text(), "# Review\n- item\n")
                self.assertNotIn("run_command", set().union(*seen_tools))
                self.assertEqual([item["path"] for item in result["outputs"]], ["review-2026-11-25.md"])
                self.assertTrue(result["outputs"][0].get("undo"))
                self.assertEqual(server.WORKSPACES, {}, "the run's folder grant is revoked afterwards")

                # The same result in the inbox can be undone (even when opened from another port), removing the new file.
                self.assertEqual(result.get("undoPort"), port)
                store = server.schedule_store()
                item = store.update(lambda data: Scheduler.add_inbox(
                    data, task, "complete", result["summary"], result["outputs"], WED, WED, "schedule",
                    undo_port=result.get("undoPort")))
                second_httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ChatHandler)
                Thread(target=second_httpd.serve_forever, daemon=True).start()
                self.addCleanup(second_httpd.server_close)
                self.addCleanup(second_httpd.shutdown)
                state = post(second_httpd.server_address[1], "/api/schedules/undo", {"id": item["id"]})
                self.assertFalse((folder / "review-2026-11-25.md").exists())
                self.assertTrue(state["inbox"][0]["outputs"][0]["undone"])
                with self.assertRaises(RuntimeError, msg="nothing left to undo"):
                    post(port, "/api/schedules/undo", {"id": item["id"]})

                # Running a scheduled task with an attached skill runs and reports its declarative verification checks.
                skill_replies = iter([
                    {"tool_calls": [{"function": {"name": "write_file", "arguments": {
                        "path": "digest-2026-11-25.md",
                        "content": "# Digest\n\n## Highlights\n\nSummarized [notes.md](notes.md) cleanly.\n",
                    }}}]},
                    {"content": "Saved digest-2026-11-25.md."},
                ])

                def skill_step(_handler, messages, tools, _think, _check, emit, **options):
                    emit("step")
                    if options.get("output_schema") is not None:
                        return {"content": json.dumps({"route": "agent"})}
                    return next(skill_replies, {"content": "Done."})

                with patch.object(server.ChatHandler, "agent_step", skill_step):
                    skill_task = {
                        "id": "t2", "title": "Verified digest", "prompt": "Focus on notes.md",
                        "folder": str(folder), "access": "create", "web": False,
                        "skill": {
                            "name": "weekly-digest",
                            "instructions": "Read notes.md and save a digest.",
                            "checks": [
                                {"type": "file_exists", "pattern": "*.md"},
                                {"type": "contains_heading", "text": "Highlights"},
                                {"type": "citations"},
                                {"type": "no_placeholders"},
                            ],
                        },
                    }
                    skill_res = server.run_scheduled_task(skill_task, Event(), port)
                    self.assertEqual(skill_res["status"], "complete", skill_res)
                    self.assertTrue(skill_res.get("skillChecks", {}).get("passed"), skill_res)
                    self.assertEqual(len(skill_res["skillChecks"]["results"]), 4)

                # A busy model is reported as busy, so the scheduler waits instead of failing the run.
                with server.GENERATION:
                    self.assertEqual(server.run_scheduled_task(task, Event(), port), {"status": "busy"})


class ScheduleEndpointTest(unittest.TestCase):
    def test_save_needs_a_folder_and_a_future_time_then_lists_the_task(self):
        import run as bench
        import server
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ws").mkdir()
            with patch.object(server, "SETTINGS_FILE", root / "settings.json"), patch.object(server, "WORKSPACES", {}):
                key = server.grant_workspace(server.Workspace(root / "ws"))
                with self.assertRaises(RuntimeError, msg="a new task needs a folder"):
                    bench.post("/api/schedules/save", {"task": {"prompt": "x", "schedule": {"kind": "daily", "time": "09:00"}}})
                past = {"prompt": "x", "schedule": {"kind": "once", "time": "09:00", "date": "2020-01-01"}}
                with self.assertRaises(RuntimeError, msg="a one-time task in the past is refused"):
                    bench.post("/api/schedules/save", {"task": past, "workspace": key})
                state = bench.post("/api/schedules/save", {"workspace": key, "task": {
                    "title": "Morning check", "prompt": "List what changed in the folder.",
                    "schedule": {"kind": "weekdays", "time": "07:30"}, "access": "read"}})
                task = state["task"]
                self.assertEqual(task["folder"], str((root / "ws").resolve()))
                self.assertTrue(task["nextRun"])
                listed = bench.post("/api/schedules/disable", {"id": task["id"]})
                self.assertFalse(listed["tasks"][0]["enabled"])
                self.assertIsNone(listed["tasks"][0]["nextRun"])
                self.assertEqual(bench.post("/api/schedules/delete", {"id": task["id"]})["tasks"], [])


class ScheduleSteeringAndEdgeCaseTest(unittest.TestCase):
    def test_steering_overrides_time_days_cadence_and_permissions(self):
        # Time correction must override instead of triggering "several times a day".
        p_time = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Read my notes every Wednesday at 6pm", steering=["Actually make it 7pm instead"],
        )
        self.assertNotIn("unsupported", p_time)
        self.assertEqual(p_time["task"]["schedule"], {"kind": "weekly", "time": "19:00", "days": [2]})

        # Plural or singular day correction on a weekly task keeps weekly cadence and updates the day.
        p_days = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Summarize my notes every Wednesday at 6pm", steering=["Change it to Fridays instead"],
        )
        self.assertEqual(p_days["task"]["schedule"], {"kind": "weekly", "time": "18:00", "days": [4]})
        p_day_single = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Summarize my notes every Wednesday at 6pm", steering=["Actually make it Friday instead"],
        )
        self.assertEqual(p_day_single["task"]["schedule"], {"kind": "weekly", "time": "18:00", "days": [4]})

        # Cadence change from weekly to daily or explicit one-time tomorrow.
        p_daily = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Summarize my notes every Wednesday at 6pm", steering=["Make it daily at 8am"],
        )
        self.assertEqual(p_daily["task"]["schedule"], {"kind": "daily", "time": "08:00"})
        p_once = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Summarize my notes every Wednesday at 6pm", steering=["Actually just once tomorrow at 5pm"],
        )
        self.assertEqual(p_once["task"]["schedule"], {"kind": "once", "date": "2026-11-26", "time": "17:00"})

        # Steering can fix an initially unsupported schedule.
        p_fixed = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Summarize my notes every 2 hours", steering=["Okay make it every day at 9am"],
        )
        self.assertNotIn("unsupported", p_fixed)
        self.assertEqual(p_fixed["task"]["schedule"], {"kind": "daily", "time": "09:00"})

        # Permissions resolve chronologically so steering can flip create/read and web on or off.
        p_flip_on = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Read my notes read-only every Wednesday at 6pm",
            steering=["Actually save the summary to summary.md and search the web"],
        )
        self.assertEqual((p_flip_on["task"]["access"], p_flip_on["task"]["web"]), ("create", True))
        p_flip_off = scheduler.proposal_from_tool(
            TOOL_ARGS, WED, "Save a summary to summary.md and search the web every Wednesday at 6pm",
            steering=["Make it read-only and offline"],
        )
        self.assertEqual((p_flip_off["task"]["access"], p_flip_off["task"]["web"]), ("read", False))

    def test_natural_language_edge_cases(self):
        # Concrete output filenames enable create-files access; reading an input filename stays read-only.
        p_md = scheduler.proposal_from_tool(TOOL_ARGS, WED, "Save a summary to summary.md every Friday at 5pm")
        self.assertEqual(p_md["task"]["access"], "create")
        p_read = scheduler.proposal_from_tool(TOOL_ARGS, WED, "Read notes.md and summarize it every Friday at 5pm")
        self.assertEqual(p_read["task"]["access"], "read")

        # Context-prefixed multi-sentence request still gets a schedule hint; plain statements stay None.
        self.assertIsNotNone(scheduler.schedule_hint(
            "I keep my class notes in this folder. Summarize them every Monday and Thursday at 7:30pm.", WED,
        ))
        self.assertIsNone(scheduler.schedule_hint("I have a meeting every Wednesday at 6pm.", WED))

        # Leading-zero 24h times are unambiguous; multi-day "on Mon and Thu" preserves both days; "in 2 hours" resolves.
        p_24 = scheduler.proposal_from_tool(TOOL_ARGS, WED, "Check notes every day at 08:00")
        self.assertEqual(p_24["task"]["schedule"]["time"], "08:00")
        self.assertNotIn("time", p_24["needsClarification"])

        p_multi = scheduler.proposal_from_tool(TOOL_ARGS, WED, "Summarize notes on Monday and Thursday at 5pm")
        self.assertEqual(p_multi["task"]["schedule"], {"kind": "weekly", "time": "17:00", "days": [0, 3]})
        self.assertIn("kind", p_multi["needsClarification"])

        p_rel = scheduler.proposal_from_tool(TOOL_ARGS, WED, "Check my notes in 2 hours")
        self.assertEqual(p_rel["task"]["schedule"], {"kind": "once", "date": "2026-11-25", "time": "11:00"})

    def test_invalid_cadence_and_relative_delay_corrections(self):
        timing = scheduler.resolve_timing("Check notes every 2 hours", WED, ["Make it 7pm"])
        self.assertTrue(timing["unsupported"])
        timing = scheduler.resolve_timing("Check notes every Wednesday at 6pm", WED, ["Make it next Friday"])
        self.assertEqual(timing["kind"], "once")
        timing = scheduler.read_timing("Check notes in 24 hours", WED)
        self.assertEqual((timing["date"], timing["time"]), ("2026-11-26", "09:00"))
        for request in ("Check notes in 0 minutes", "Check notes in 2 hours at 7pm", "Check notes at 25"):
            with self.subTest(request=request):
                self.assertTrue(scheduler.read_timing(request, WED)["problem"])

    def test_cancel_phrases_in_questions_and_examples_are_not_instructions(self):
        for text in ('Explain how to cancel the schedule', 'What does "cancel the schedule" mean?'):
            self.assertFalse(scheduler.cancels_schedule(text))
        self.assertTrue(scheduler.cancels_schedule("Actually, cancel the schedule"))
        self.assertFalse(scheduler.runs_now("Cancel the schedule and don't just run it now"))

    def test_rejected_schedule_checkpoint_can_resume_through_work_endpoint(self):
        import run as bench
        import server
        from uuid import uuid4
        seen = []
        def step(_handler, messages, tools, _think, _check, emit, **options):
            seen.append({tool["function"]["name"] for tool in tools})
            return {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "ws"
            folder.mkdir()
            sessions = server.BrowserSessions()
            with patch.object(server, "CURRENT", {"model": "replay", "num_ctx": 8192}), \
                    patch.object(server, "SETTINGS_FILE", root / "settings.json"), \
                    patch.object(server, "SESSIONS", sessions), patch.object(server, "WORKSPACES", {}), \
                    patch.object(sessions, "ensure_loaded"), patch.object(server.ChatHandler, "agent_step", step):
                key = server.grant_workspace(server.Workspace(folder))
                checkpoint = str(uuid4())
                base = {"workspace": key, "mode": "ask", "web": False, "allowMemory": False}
                with bench.request("/api/agent/run", {**base, "task": "Read notes every 2 hours",
                                                     "checkpoint": checkpoint}) as response:
                    first = [json.loads(line) for line in response]
                self.assertFalse(seen)
                self.assertTrue(any(e.get("scheduleStatus") == "rejected" for e in first))
                with bench.request("/api/agent/run", {**base, "task": "Make it daily at 9am",
                                                     "resume": checkpoint, "steering": "Make it daily at 9am"}) as response:
                    run_id = None
                    approval = None
                    for line in response:
                        event = json.loads(line)
                        if event["event"] == "run":
                            run_id = event["id"]
                        if event["event"] == "approval":
                            approval = event
                            bench.post("/api/agent/stop", {"run": run_id})
                self.assertIsNotNone(approval)
                self.assertEqual(approval["detail"]["task"]["schedule"], {"kind": "daily", "time": "09:00"})
                self.assertEqual(seen, [{"schedule_task"}])

    def test_cancel_only_never_executes_and_run_now_disables_schedule_tool(self):
        import agent
        for note, run_now in (("Cancel the schedule", False), ("Cancel the schedule and do it now", True)):
            with self.subTest(note=note), TemporaryDirectory() as directory:
                queue = []
                calls = []
                def model(messages, tools, think, **kwargs):
                    calls.append({tool["function"]["name"] for tool in tools})
                    if len(calls) == 1:
                        return {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]}
                    if len(calls) == 2:
                        return {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]}
                    return {"content": "Done."}
                def approve(*args):
                    queue.append({"id": "s1", "text": note})
                    raise agent.ApprovalCancelled()
                def take():
                    notes = list(queue)
                    queue.clear()
                    return notes
                runner = agent.Agent(model, lambda *a, **k: None, lambda: None, approve,
                                     workspace=agent.Workspace(directory), mode="auto", web=False, memory=False,
                                     schedule=lambda task: self.fail("stale proposal saved"))
                runner.run("system", [], "Read notes every Wednesday at 6pm", take_steering=take,
                           steering_pending=lambda: bool(queue))
                self.assertEqual(len(calls), 3 if run_now else 1)
                if run_now:
                    self.assertNotIn("schedule_task", calls[1])

    def test_unsupported_schedule_uses_zero_inference_calls_and_schedule_only_disables_think(self):
        import agent
        with TemporaryDirectory() as directory:
            ws = agent.Workspace(directory)
            # 1. Unsupported schedule short-circuits before calling the model at all.
            calls = []
            events = []
            runner = agent.Agent(
                lambda *a, **k: calls.append(a),
                lambda ev, **f: events.append((ev, f)),
                lambda: None, lambda *a: self.fail("unexpected approval"),
                workspace=ws, mode="ask", web=False, memory=False, think=True,
                schedule=lambda task: self.fail("unexpected save"),
            )
            runner.run("system", [], "Check my notes every 2 hours and summarize changes")
            self.assertEqual(calls, [], "unsupported schedule must not waste an LLM call")
            self.assertIn("every hour or minute", next(f["text"] for ev, f in events if ev == "result"))

            # 2. Valid schedule_only run passes think=False even when Agent(..., think=True).
            think_flags = []
            def model(messages, tools, think, **_k):
                think_flags.append(think)
                return {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]}
            runner2 = agent.Agent(
                model, lambda *a, **k: None, lambda: None, lambda *a: True,
                workspace=ws, mode="ask", web=False, memory=False, think=True,
                schedule=lambda task: {**task, "nextRun": "2026-12-02T18:00:00"},
            )
            runner2.run("system", [], "Summarize my notes every Wednesday at 6pm")
            self.assertEqual(think_flags, [False])

    def test_steering_can_cancel_schedule_and_run_immediately(self):
        import agent
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.md").write_text("Hello notes\n", encoding="utf-8")
            ws = agent.Workspace(root)
            seen_tools = []
            steered = [False]
            steering_queue = []

            def model(messages, tools, think, **_k):
                names = [t["function"]["name"] for t in tools]
                seen_tools.append(names)
                if len(seen_tools) == 1:
                    return {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]}
                if len(seen_tools) == 2:
                    return {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "notes.md", "start_line": 1, "count": 20}}}]}
                return {"content": "Read notes.md now instead of scheduling."}

            def approve(kind, title, detail):
                if kind == "schedule" and not steered[0]:
                    steered[0] = True
                    steering_queue.append({"id": "s1", "text": "Cancel the schedule and just do it now"})
                    raise agent.ApprovalCancelled()
                return True

            def take_steering():
                out = list(steering_queue)
                steering_queue.clear()
                return out

            runner = agent.Agent(
                model, lambda *a, **k: None, lambda: None, approve,
                workspace=ws, mode="read", web=False, memory=False,
                schedule=lambda task: self.fail("should not schedule after cancel steering"),
            )
            runner.run("system", [], "Summarize my notes every Wednesday at 6pm",
                       take_steering=take_steering, steering_pending=lambda: bool(steering_queue))
            self.assertEqual(seen_tools[0], ["schedule_task"])
            self.assertIn("read_file", seen_tools[1])

    def test_resume_after_rejected_schedule_allows_steered_retry(self):
        import agent
        with TemporaryDirectory() as directory:
            ws = agent.Workspace(directory)
            saved = []
            def model(messages, tools, think, **_k):
                return {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]}
            runner = agent.Agent(
                model, lambda *a, **k: None, lambda: None, lambda *a: True,
                workspace=ws, mode="ask", web=False, memory=False,
                schedule=lambda task: saved.append(task) or {**task, "nextRun": "2026-11-27T09:00:00"},
            )
            prior = {
                "activity": [{"name": "schedule_task", "scheduleStatus": "rejected", "cancelled": False, "preview": "Old rejection"}],
                "evidence": [],
            }
            steering = [{"id": "s1", "text": "Okay make it every day at 9am"}]
            runner.run("system", [], "Summarize my notes every 2 hours", saved=prior,
                       take_steering=lambda: [steering.pop(0)] if steering else [],
                       steering_pending=lambda: bool(steering))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["schedule"], {"kind": "daily", "time": "09:00"})


class PlainTextToolCallRecoveryTest(unittest.TestCase):
    def test_recovers_standalone_calls_without_executing_examples(self):
        import agent
        allowed = {"schedule_task", "list_files"}

        # 1. Qwen <tool_call> XML tag (including unclosed tag)
        calls, prose = agent.extract_text_tool_calls(
            'Sure, setting that up.\n<tool_call>\n{"name": "schedule_task", "arguments": {"title": "Notes", "prompt": "Read notes", "repeat": "weekly", "days": ["Wed"], "time": "18:00"}}\n</tool_call>',
            allowed,
        )
        self.assertEqual(calls, [])
        self.assertIn("Sure, setting that up.", prose)

        calls_unclosed, _ = agent.extract_text_tool_calls(
            '<tool_call>{"name": "list_files", "parameters": {"path": "."}}',
            allowed,
        )
        self.assertEqual(calls_unclosed, [{"function": {"name": "list_files", "arguments": {"path": "."}}}])

        # 2. Fenced JSON tool call
        calls_fenced, _ = agent.extract_text_tool_calls(
            '```json\n{"name": "list_files", "arguments": {"path": "."}}\n```',
            allowed,
        )
        self.assertEqual(calls_fenced, [])
        calls_bare, _ = agent.extract_text_tool_calls('list_files {"path": "."}', allowed)
        self.assertEqual(calls_bare[0]["function"]["name"], "list_files")
        calls_write, _ = agent.extract_text_tool_calls(
            '<tool_call>{"name":"write_file","arguments":{"path":"a.txt","content":"bad"}}</tool_call>',
            {"write_file"},
        )
        self.assertEqual(calls_write, [])

        # 3. Disallowed tool names and ordinary JSON prose are never treated as tool calls
        calls_bad, orig = agent.extract_text_tool_calls(
            '<tool_call>{"name": "run_command", "arguments": {"command": "ls"}}</tool_call>',
            allowed,
        )
        self.assertEqual(calls_bad, [])
        self.assertIn("run_command", orig)

        calls_plain, _ = agent.extract_text_tool_calls('{"name": "Alice", "role": "student"}', allowed)
        self.assertEqual(calls_plain, [])

    def test_agent_executes_plain_text_schedule_task_without_extra_retry(self):
        import agent
        with TemporaryDirectory() as directory:
            ws = agent.Workspace(directory)
            model_calls = [0]
            saved = []

            def model(messages, tools, think, **_k):
                model_calls[0] += 1
                return {
                    "content": '<tool_call>\n{"name": "schedule_task", "arguments": {"title": "Weekly Summary", "prompt": "Summarize notes", "repeat": "weekly", "days": ["Wed"], "time": "18:00"}}\n</tool_call>',
                    "tool_calls": [],
                }

            runner = agent.Agent(
                model, lambda *a, **k: None, lambda: None, lambda *a: True,
                workspace=ws, mode="ask", web=False, memory=False,
                schedule=lambda task: saved.append(task) or {**task, "nextRun": "2026-12-02T18:00:00"},
            )
            runner.run("system", [], "Summarize my notes every Wednesday at 6pm")
            self.assertEqual(model_calls[0], 1, "recovered plain-text tool call in 1 step with no retry")
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["schedule"], {"kind": "weekly", "time": "18:00", "days": [2]})

    def test_human_schedule_formatting_and_receipt_payload(self):
        import agent
        self.assertEqual(scheduler.format_clock_human("00:00"), "12:00 AM")
        self.assertEqual(scheduler.format_clock_human("09:05"), "9:05 AM")
        self.assertEqual(scheduler.format_clock_human("12:00"), "12:00 PM")
        self.assertEqual(scheduler.format_clock_human("18:30"), "6:30 PM")
        self.assertEqual(scheduler.describe_schedule_human({"kind": "daily", "time": "08:00"}), "every day at 8:00 AM")
        self.assertEqual(scheduler.describe_schedule_human({"kind": "weekdays", "time": "07:30"}), "on weekdays at 7:30 AM")
        self.assertEqual(scheduler.describe_schedule_human({"kind": "weekly", "days": [2], "time": "18:30"}),
                         "every Wednesday at 6:30 PM")
        self.assertEqual(scheduler.describe_schedule_human({"kind": "weekly", "days": [0, 3], "time": "19:30"}),
                         "every Monday and Thursday at 7:30 PM")
        self.assertEqual(scheduler.describe_schedule_human({"kind": "weekly", "days": [0, 2, 4], "time": "09:00"}),
                         "every Monday, Wednesday, and Friday at 9:00 AM")
        self.assertEqual(scheduler.describe_schedule_human({"kind": "once", "date": "2026-12-02", "time": "18:30"}),
                         "once on Wed, Dec 2 at 6:30 PM")
        self.assertEqual(scheduler.format_moment_human("2026-11-26T18:30:00", now=THU), "today at 6:30 PM")
        self.assertEqual(scheduler.format_moment_human("2026-11-27T09:00:00", now=THU), "tomorrow at 9:00 AM")
        self.assertEqual(scheduler.format_moment_human("2026-12-02T18:30:00", now=THU), "Wed, Dec 2 at 6:30 PM")

        with TemporaryDirectory() as directory:
            ws = agent.Workspace(directory)
            events = []
            runner = agent.Agent(
                lambda messages, tools, think, **_k: {"tool_calls": [{"function": {"name": "schedule_task", "arguments": TOOL_ARGS}}]},
                lambda event, **fields: events.append((event, fields)),
                lambda: None, lambda *a: True,
                workspace=ws, mode="ask", web=False, memory=False,
                schedule=lambda task: {**task, "id": "task-123", "nextRun": "2026-12-02T18:00:00"},
            )
            runner.run("system", [], "Summarize my notes every Wednesday at 6pm")
            outcome = next(fields for event, fields in events if event == "tool_result")
            self.assertEqual(outcome["scheduleStatus"], "saved")
            self.assertEqual(outcome["scheduledTask"]["id"], "task-123")
            self.assertEqual(outcome["scheduledTask"]["folder"], ws.path.name)
            answer = next(fields["text"] for event, fields in events if event == "result")
            self.assertIn("every Wednesday at 6:00 PM", answer)
            self.assertNotIn("2026-12-02T18:00:00", answer, "reply should use human-readable time, not raw ISO")


if __name__ == "__main__":
    unittest.main()


class ChatScheduleTest(unittest.TestCase):
    """Chat can propose a scheduled task too: the server sends one card to the page and never creates the task."""

    def test_card_is_sent_once_and_questions_or_unsupported_repeats_get_none(self):
        import server
        sent = []
        request = "Every Wednesday at 6 pm, check the notes in this folder. Set a task for me."
        result = server.ChatHandler.chat_schedule_result(dict(TOOL_ARGS, time="06:00"), request, False, sent.append)
        self.assertTrue(result.startswith("A confirmation card"))
        self.assertEqual(sent[0]["schedule"]["task"]["schedule"], {"kind": "weekly", "time": "18:00", "days": [2]})
        self.assertIn("Nothing is scheduled until the user clicks Create task", result)
        again = server.ChatHandler.chat_schedule_result(TOOL_ARGS, request, True, sent.append)
        self.assertIn("already shown", again)
        for words in ("Why do we have lab every Wednesday at 6pm?", "Every other Wednesday at 6pm, check my notes"):
            with self.subTest(words=words):
                self.assertTrue(server.ChatHandler.chat_schedule_result(TOOL_ARGS, words, False, sent.append).startswith("Not scheduled"))
        self.assertEqual(len(sent), 1, "only the first proposal became a card")

    def test_only_messages_naming_a_time_or_repeat_are_offered_the_tool(self):
        import run as bench
        import server
        calls = []

        def with_tools(handler, messages, think, system, temperature, web, schedule=None):
            calls.append(("tools", web, schedule))
            handler.send_json(200, {"ok": True})

        def plain(handler, messages, think, compact=False, system="", temperature=None):
            calls.append(("plain",))
            handler.send_json(200, {"ok": True})

        with patch.object(server.ChatHandler, "relay_chat_with_web", with_tools), \
                patch.object(server.ChatHandler, "relay_chat", plain), patch.object(server, "SESSIONS") as sessions:
            sessions.ensure_loaded.return_value = None
            for text in ("Every Wednesday at 6 pm, check my notes. Set a task for me.", "What is photosynthesis?",
                         "Why do we have lab every Wednesday at 6pm?"):
                bench.post("/api/chat", {"messages": [{"role": "user", "content": text}], "web": "off", "scheduleText": text})
        self.assertEqual(calls[0][0], "tools")
        self.assertEqual(calls[0][2][1], "every Wed at 18:00")
        self.assertEqual(calls[1:], [("plain",), ("plain",)])


    def test_voice_chat_never_routes_tools(self):
        import run as bench
        import server
        calls = []

        def plain(handler, messages, think, compact=False, system="", temperature=None):
            calls.append(messages[-1]["content"])
            handler.send_json(200, {"ok": True})

        text = "Every Wednesday at 6 pm, push to GitHub and read Google Drive."
        payload = {"messages": [{"role": "user", "content": text}], "voice": True}
        with patch.object(server.ChatHandler, "relay_chat", plain), \
                patch.object(server, "connector_routes") as route, \
                patch.object(server.ChatHandler, "relay_chat_with_web") as tools, \
                patch.object(server, "SESSIONS"):
            self.assertEqual(bench.post("/api/chat", payload), {"ok": True})
            for extra in ({"web": "always"}, {"compact": True}, {"scheduleText": text},
                          {"requestText": text}, {"githubRoute": False}, {"driveRoute": False},
                          {"githubGeneration": 0}, {"driveGeneration": 0}, {"voice": "true"}):
                with self.subTest(extra=extra), self.assertRaisesRegex(RuntimeError, "HTTP 400"):
                    bench.post("/api/chat", dict(payload, **extra))
            route.assert_not_called()
            tools.assert_not_called()
        self.assertEqual(calls, [text])


class RunHistoryTest(unittest.TestCase):
    """Each task keeps a History of its runs; every run keeps its timeline so it can open as a chat."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name))
        self.task = clean_task({"prompt": "Check the folder", "schedule": {"kind": "daily", "time": "09:00"}})
        self.task.update(folder="/tmp/x", nextRun=scheduler.iso(WED - datetime.timedelta(minutes=1)))
        self.store.update(lambda data: data["tasks"].append(dict(self.task)))
        self.seen_running = []

    def run_with(self, result, events=()):
        def fake(task, stop_event, log=None):
            self.seen_running.append([run["status"] for run in self.store.load()["runs"]])
            for event in events:
                log.add(event)
            return result
        scheduler.Scheduler(self.store, fake, clock=lambda: WED).tick()

    def test_a_run_is_in_history_while_running_and_keeps_its_timeline(self):
        events = [{"event": "run", "id": "r", "workspacePath": "/tmp/x"}, {"event": "step"},
                  {"event": "delta", "text": "tok"}, {"event": "tool", "id": "t1", "name": "list_files", "label": "List ."},
                  {"event": "tool_result", "id": "t1", "ok": True, "preview": "a.txt"}, {"event": "result", "text": "Done."}]
        self.run_with({"status": "complete", "summary": "Done.", "outputs": []}, events)
        self.assertEqual(self.seen_running, [["running"]], "the entry exists as soon as the run starts")
        data = self.store.load()
        run = data["runs"][0]
        self.assertEqual((run["status"], run["taskId"], run["timeline"], run["read"]), ("complete", self.task["id"], True, False))
        self.assertEqual(data["inbox"][0]["id"], run["id"], "the inbox item and the history entry share an id")
        saved = scheduler.load_run_log(self.store, run["id"])
        self.assertEqual([event["event"] for event in saved["events"]], ["run", "step", "tool", "tool_result", "result"],
                         "token-by-token text is left out of the saved timeline")
        self.assertEqual(saved["prompt"], "Check the folder")

    def test_a_run_that_never_started_leaves_no_entry_and_an_interrupted_one_is_marked_stopped(self):
        self.run_with({"status": "busy"})
        self.assertEqual(self.store.load()["runs"], [])
        self.store.update(lambda data: scheduler.record_run(data, self.task, "a" * 32, status="running", startedAt="2026-11-25T09:00:00"))
        with patch("scheduler.threading.Thread"):   # start() without the background loop
            scheduler.Scheduler(self.store, lambda *a, **k: None, clock=lambda: WED).start()
        run = scheduler.find(self.store.load()["runs"], "a" * 32)
        self.assertEqual(run["status"], "stopped")
        self.assertIn("closed", run["summary"])

    def test_history_is_bounded_per_task(self):
        def fill(data):
            for index in range(scheduler.RUN_HISTORY_PER_TASK + 5):
                scheduler.record_run(data, self.task, f"{index:032x}", status="complete")
        self.store.update(fill)
        self.assertEqual(len(self.store.load()["runs"]), scheduler.RUN_HISTORY_PER_TASK)


class RunHistoryEndpointTest(unittest.TestCase):
    def test_history_run_read_and_delete(self):
        import server
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(server, "SETTINGS_FILE", root / "settings.json"), patch.object(server, "SCHEDULER", None):
                task = clean_task({"prompt": "Check", "schedule": {"kind": "daily", "time": "09:00"}})
                task["folder"] = str(root)
                store = server.schedule_store()
                store.update(lambda data: data["tasks"].append(task))
                log = scheduler.RunLog("b" * 32)
                log.add({"event": "result", "text": "All good."})
                scheduler.save_run_log(store, log, task)
                store.update(lambda data: Scheduler.add_inbox(data, task, "complete", "All good.", [], WED, WED, "schedule",
                                                              item_id="b" * 32, timeline=True))
                self.assertEqual(server.schedule_state()["tasks"][0]["unreadRuns"], 1)
                history = server.schedule_history(task["id"])["runs"]
                self.assertEqual([(run["id"], run["status"]) for run in history], [("b" * 32, "complete")])
                run = server.schedule_run("b" * 32)
                self.assertEqual((run["live"], run["events"][0]["text"], run["prompt"]), (False, "All good.", "Check"))
                self.assertIsNone(server.schedule_run("c" * 32))
                store.update(lambda data: [item.update(read=True) for item in [*data["inbox"], *data["runs"]] if item["id"] == "b" * 32])
                self.assertEqual(server.schedule_state()["tasks"][0]["unreadRuns"], 0)
                handler = Mock()
                handler.read_json.return_value = {"id": task["id"]}
                server.ChatHandler.schedule_action(handler, "delete")
                self.assertEqual(server.schedule_store().load()["runs"], [])
                self.assertFalse(scheduler.run_file(store, "b" * 32).exists(), "the timeline file is removed too")
