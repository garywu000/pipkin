"""Scheduled background tasks: schedule math, missed runs, waiting for a busy model, and unattended runs."""
import datetime
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

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

    def fake_run(self, task, stop_event):
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
        self.scheduler.run_task = lambda task, stop: 1 / 0
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


if __name__ == "__main__":
    unittest.main()
