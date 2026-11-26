"""Scheduled background tasks: when they run, where their results go, and the loop that runs them.

Tasks run only while Pipkin is open. A run that came due while Pipkin was closed or the computer was asleep is not
started on its own: the task is marked missed so the user can run or skip it. Every run is unattended, so the server
starts it read-only or new-files-only, without shell commands, and with hard step and time limits. A run waits while
the model is busy or memory pressure is critical, and is skipped if that lasts too long.
"""
import datetime
from contextlib import contextmanager
import json
import os
import sys
import threading
import uuid
from pathlib import Path

import skills as skills_module

KINDS = ("once", "daily", "weekdays", "weekly")
ACCESS = ("read", "create")
MISSED_GRACE = datetime.timedelta(minutes=10)    # a run this late counts as missed instead of starting by itself
DEFER_RETRY = datetime.timedelta(minutes=1)
DEFER_LIMIT = datetime.timedelta(minutes=45)     # stop waiting for a busy model or memory and record a skip
TICK_SECONDS = 30
TASK_LIMIT = 30
INBOX_LIMIT = 60
TITLE_LIMIT = 80
PROMPT_LIMIT = 4000


def iso(moment):
    return moment.isoformat(timespec="seconds") if moment else None


def parse_iso(text):
    try:
        return datetime.datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def parse_clock(text):
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        raise ValueError("Choose a time.")
    if not (text[:2].isascii() and text[:2].isdigit() and text[3:].isascii() and text[3:].isdigit()):
        raise ValueError("Choose a valid time.")
    hour, minute = int(text[:2]), int(text[3:])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("Choose a valid time.")
    return hour, minute


def next_run(schedule, after):
    """The first moment strictly after `after` (local, naive) when the schedule fires, or None when it never will."""
    hour, minute = parse_clock(schedule["time"])
    at_time = datetime.time(hour, minute)
    if schedule["kind"] == "once":
        moment = datetime.datetime.combine(datetime.date.fromisoformat(schedule["date"]), at_time)
        return moment if moment > after else None
    days = {"daily": range(7), "weekdays": range(5)}.get(schedule["kind"], schedule.get("days") or [])
    for offset in range(8):
        day = after.date() + datetime.timedelta(days=offset)
        if day.weekday() in days:
            moment = datetime.datetime.combine(day, at_time)
            if moment > after:
                return moment
    return None


def clean_schedule(raw):
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise ValueError("Choose how often the task runs.")
    schedule = {"kind": raw["kind"], "time": raw.get("time")}
    parse_clock(schedule["time"])
    if schedule["kind"] == "once":
        try:
            schedule["date"] = datetime.date.fromisoformat(raw.get("date")).isoformat()
        except (TypeError, ValueError):
            raise ValueError("Choose a date for the one-time run.") from None
    if schedule["kind"] == "weekly":
        days = raw.get("days")
        if (not isinstance(days, list) or not days or len(days) > 7
                or not all(type(day) is int and 0 <= day <= 6 for day in days)):
            raise ValueError("Choose at least one day of the week.")
        schedule["days"] = sorted(set(days))
    return schedule


def clean_task_skill(raw):
    """Validate an optional skill attached to a scheduled task (text instructions + declarative checks only)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Invalid skill for scheduled task.")
    name_raw = raw.get("name")
    if not isinstance(name_raw, str):
        raise ValueError("Invalid skill name.")
    name = name_raw.strip().lstrip("/").lower()
    if not skills_module.NAME_RE.fullmatch(name):
        raise ValueError("Invalid skill name.")
    instructions = raw.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > PROMPT_LIMIT:
        raise ValueError("Invalid skill instructions.")
    checks = skills_module.normalize_checks(raw.get("checks"))
    output = str(raw.get("output") or "").strip().lower()
    if output not in skills_module.ALLOWED_OUTPUTS:
        output = ""
    out = {"name": name, "instructions": instructions.strip()}
    if checks:
        out["checks"] = checks
    if output:
        out["output"] = output
    return out


def clean_task(raw, existing=None):
    """Validate a task from the page. The folder is filled in by the server from an authorized workspace."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid task.")
    skill = clean_task_skill(raw.get("skill")) if "skill" in raw else (existing or {}).get("skill")
    title = raw.get("title")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        prompt = ""
    if not prompt.strip() and not skill:
        raise ValueError("Describe what the task should do.")
    if len(prompt) > PROMPT_LIMIT:
        raise ValueError(f"Keep the instructions under {PROMPT_LIMIT:,} characters.")
    if not isinstance(title, str) or not title.strip():
        fallback = f"/{skill['name']}" + (f" {prompt.strip()}" if prompt.strip() else "") if skill else prompt
        title = " ".join(fallback.split())[:60]
    title = " ".join(title.split())[:TITLE_LIMIT]
    access = raw.get("access", "read")
    if access not in ACCESS:
        raise ValueError("Choose what the task may do.")
    for flag in ("web", "enabled"):
        if not isinstance(raw.get(flag, True), bool):
            raise ValueError("Invalid task.")
    task = dict(existing or {})
    task.update(title=title, prompt=prompt.strip(), schedule=clean_schedule(raw.get("schedule")), access=access,
                web=raw.get("web", False), enabled=raw.get("enabled", True))
    if skill:
        task["skill"] = skill
    else:
        task.pop("skill", None)
    task.setdefault("id", uuid.uuid4().hex)
    task.setdefault("created", iso(datetime.datetime.now()))
    return task


class ProcessLock:
    """Only one Pipkin server on this computer runs scheduled tasks (the dev window runs a second server)."""

    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                handle = open(self.path, "a+b")
            else:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                handle = os.fdopen(fd, "r+b")
        except OSError:
            return False
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True


class Store:
    """Tasks and the inbox in one private JSON file, written atomically."""

    lock = threading.RLock()  # shared, so every Store for the same file in this process serializes its writes

    def __init__(self, folder):
        self.path = Path(folder) / "schedules.json"

    def load(self):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("version", 1)
            data.setdefault("paused", False)
            data["tasks"] = [task for task in data.get("tasks", []) if isinstance(task, dict) and task.get("id")]
            data["inbox"] = [item for item in data.get("inbox", []) if isinstance(item, dict) and item.get("id")]
            return data

    def save(self, data):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            text = json.dumps(data, ensure_ascii=False, indent=1)
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.path)
            except OSError:
                temp.unlink(missing_ok=True)
                raise

    @contextmanager
    def transaction_lock(self):
        # The dev and installed servers can both edit the same store. Serialize the
        # entire read-modify-write transaction across processes, not just threads.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".write.lock")
        if sys.platform == "win32":
            handle = open(lock_path, "a+b")
        else:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            handle = os.fdopen(fd, "r+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
        finally:
            handle.close()

    def update(self, change):
        with self.lock, self.transaction_lock():
            data = self.load()
            original = json.dumps(data, sort_keys=True)
            result = change(data)
            if json.dumps(data, sort_keys=True) != original:
                self.save(data)
            return result


def find(items, item_id):
    return next((item for item in items if item.get("id") == item_id), None)


class Scheduler:
    """Wakes every half minute, starts due tasks one at a time, and records each outcome in the inbox.

    `run_task(task, stop_event)` does the actual work and returns
    {"status": "complete" | "stopped" | "error" | "busy", "summary": str, "outputs": [...]}."""

    def __init__(self, store, run_task, *, pressure=lambda: "normal", busy=lambda: False,
                 clock=datetime.datetime.now, lock=None):
        self.store = store
        self.run_task = run_task
        self.pressure = pressure
        self.busy = busy
        self.clock = clock
        self.lock = lock
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.manual = []                 # task ids queued with Run now
        self.manual_lock = threading.Lock()
        self.deferred = {}               # task id -> when it first had to wait
        self.running = None
        self.last_tick = None
        self.thread = None

    # ----- control from the HTTP handlers -----

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.loop, name="pipkin-scheduler", daemon=True)
            self.thread.start()

    def run_now(self, task_id):
        with self.manual_lock:
            if task_id not in self.manual and (self.running or {}).get("taskId") != task_id:
                self.manual.append(task_id)
        self.wake.set()

    def stop_current(self):
        if self.running:
            self.stop_event.set()
            return True
        return False

    def poke(self):
        self.wake.set()

    def state(self):
        current = self.running
        running = dict(current) if current else None
        with self.manual_lock:
            waiting = list(self.manual)
        return {"running": running, "queued": waiting,
                "active": bool(self.lock is None or self.lock.handle is not None)}

    # ----- the loop -----

    def loop(self):
        while True:
            try:
                if self.lock is None or self.lock.acquire():
                    self.tick()
            except Exception as error:  # noqa: BLE001 - the scheduler must keep running
                print(f"Scheduler error: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            self.wake.wait(TICK_SECONDS)
            self.wake.clear()

    def tick(self):
        now = self.clock()
        self.last_tick = now
        due = self.store.update(lambda data: self.plan(data, now))
        with self.manual_lock:
            manual, self.manual = self.manual, []
        for task_id in manual:
            # A manual run of an already-due task satisfies that occurrence.
            trigger = "schedule" if task_id in due else "manual"
            self.execute(task_id, trigger=trigger)
            if task_id in due:
                due.remove(task_id)
        for task_id in due:
            self.execute(task_id, trigger="schedule")

    def plan(self, data, now):
        """Advance every task's next run; return the ids that should start now, oldest first."""
        due = []
        for task in data["tasks"]:
            schedule = task.get("schedule")
            if not schedule:
                continue
            upcoming = parse_iso(task.get("nextRun"))
            if not task.get("enabled", True):
                task["nextRun"] = None
                task.pop("waitReason", None)
                self.deferred.pop(task["id"], None)
                continue
            if upcoming is None:
                upcoming = next_run(schedule, max(now, parse_iso(task.get("created")) or now) - datetime.timedelta(seconds=1))
                task["nextRun"] = iso(upcoming)
                if upcoming is None:
                    task["enabled"] = False
                    continue
            if upcoming > now:
                continue
            if data.get("paused"):
                # While intentionally paused, quietly roll past expired occurrences instead of flagging them "missed while closed".
                task.pop("waitReason", None)
                self.deferred.pop(task["id"], None)
                if now - upcoming > MISSED_GRACE:
                    self.advance(task, now)
                continue
            waiting_since = self.deferred.get(task["id"])
            if waiting_since is None and now - upcoming > MISSED_GRACE:
                # Due while Pipkin was closed or asleep: offer it instead of starting work the user didn't expect.
                task["missed"] = iso(upcoming)
                self.advance(task, now)
                continue
            if waiting_since is not None and now - waiting_since > DEFER_LIMIT:
                self.deferred.pop(task["id"], None)
                reason = task.pop("waitReason", None) or "the model was busy"
                self.add_inbox(data, task, "skipped", f"Skipped because {reason} for more than 45 minutes.", [], now, now,
                               "schedule")
                self.advance(task, now)
                continue
            # Tasks already queued behind another task must not become missed.
            self.deferred.setdefault(task["id"], now)
            due.append((upcoming, task["id"]))
        return [task_id for _, task_id in sorted(due)]

    def advance(self, task, now):
        upcoming = next_run(task["schedule"], now)
        task["nextRun"] = iso(upcoming)
        if upcoming is None:
            task["enabled"] = False

    def execute(self, task_id, *, trigger):
        data = self.store.load()
        task = find(data["tasks"], task_id)
        if task is None:
            return
        now = self.clock()
        if trigger == "schedule":
            upcoming = parse_iso(task.get("nextRun"))
            if data.get("paused") or not task.get("enabled", True) or upcoming is None or upcoming > now:
                self.deferred.pop(task_id, None)
                return
        wait = "the model was busy" if self.busy() else "memory pressure was critical" if self.pressure() == "critical" else None
        if wait:
            if trigger == "manual":
                self.store.update(lambda d: self.add_inbox(
                    d, task, "skipped", f"Not started because {wait}. Try Run now again in a moment.", [], now, now, trigger))
            else:
                self.deferred.setdefault(task_id, now)
                self.store.update(lambda d: (find(d["tasks"], task_id) or {}).update(waitReason=wait))
            return
        self.stop_event.clear()
        self.running = {"taskId": task_id, "title": task["title"], "startedAt": iso(now), "trigger": trigger}
        try:
            result = self.run_task(dict(task), self.stop_event)
        except Exception as error:  # noqa: BLE001 - one broken run must not stop the scheduler
            result = {"status": "error", "summary": f"Pipkin hit an unexpected error ({type(error).__name__}).",
                      "outputs": []}
        finally:
            self.running = None
        ended = self.clock()
        if result.get("status") == "busy":
            if trigger == "manual":
                self.store.update(lambda d: self.add_inbox(
                    d, task, "skipped", "Not started because the model was busy. Try Run now again in a moment.", [],
                    now, ended, trigger))
            else:
                self.deferred.setdefault(task_id, now)
                self.store.update(lambda d: (find(d["tasks"], task_id) or {}).update(waitReason="the model was busy"))
            return
        self.deferred.pop(task_id, None)
        status = result.get("status") if result.get("status") in ("complete", "stopped", "error") else "error"

        def record(d):
            current = find(d["tasks"], task_id)
            if current is not None:
                current.pop("waitReason", None)
                current["lastRun"] = iso(ended)
                current["lastStatus"] = status
                # The task may have been edited, disabled or rescheduled while it ran.
                if current.get("revision") == task.get("revision") and current.get("nextRun") == task.get("nextRun"):
                    current.pop("missed", None)
                    if trigger == "schedule" and current.get("enabled", True):
                        self.advance(current, ended)
            self.add_inbox(d, task, status, result.get("summary") or "", result.get("outputs") or [],
                           now, ended, trigger, undo_port=result.get("undoPort"),
                           skill_checks=result.get("skillChecks"))
        self.store.update(record)

    @staticmethod
    def add_inbox(data, task, status, summary, outputs, started, ended, trigger, *, undo_port=None, skill_checks=None):
        item = {
            "id": uuid.uuid4().hex, "taskId": task.get("id"), "title": task.get("title", "Scheduled task"),
            "status": status, "summary": summary[:20_000], "outputs": outputs[:40], "folder": task.get("folder"),
            "access": task.get("access"), "startedAt": iso(started), "endedAt": iso(ended), "trigger": trigger,
            "read": False,
        }
        if isinstance(undo_port, int) and 1 <= undo_port <= 65535:
            item["undoPort"] = undo_port
        if isinstance(skill_checks, dict) and isinstance(skill_checks.get("results"), list) and skill_checks["results"]:
            item["skillChecks"] = skill_checks
        data["inbox"].insert(0, item)
        del data["inbox"][INBOX_LIMIT:]
        return item
