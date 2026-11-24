"""Run Pipkin Work benchmarks against a scripted or installed local model."""

import argparse
import base64
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cases import CASES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server

PORT = 8766


@contextmanager
def request(path, payload, timeout=15):
    """Use the real handler without binding a second HTTP port."""
    client, worker = socket.socketpair()
    client.settimeout(timeout)

    def serve():
        with worker:
            server.ChatHandler(worker, ("127.0.0.1", 3501), SimpleNamespace(server_port=PORT))

    thread = Thread(target=serve, daemon=True)
    thread.start()
    data = json.dumps(payload).encode()
    origin = f"http://127.0.0.1:{PORT}"
    headers = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nOrigin: {origin}\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n")
    try:
        with client, client.makefile("rb") as response:
            client.sendall(headers.encode() + data)
            _, status, _ = response.readline().split(maxsplit=2)
            while response.readline() not in (b"\r\n", b""):
                pass
            if status != b"200":
                raise RuntimeError(f"{path} returned HTTP {status.decode()}.")
            yield response
    finally:
        thread.join(timeout=5)
        if thread.is_alive():
            server.SESSIONS.cancel_generation()
            thread.join(timeout=5)
            raise RuntimeError(f"{path} did not shut down promptly.")


def post(path, payload):
    with request(path, payload) as response:
        return json.load(response)


def ollama_json(path):
    connection, response = server.ollama_request("GET", path, timeout=5)
    try:
        if response.status != 200:
            raise RuntimeError(f"Ollama returned HTTP {response.status} for {path}.")
        return json.load(response)
    finally:
        connection.close()


def memory_free_percent():
    if platform.system() != "Darwin":
        return None
    output = subprocess.check_output(["/usr/bin/memory_pressure", "-Q"], text=True, timeout=3)
    match = re.search(r"System-wide memory free percentage: (\d+)%", output)
    return int(match.group(1)) if match else None


@contextmanager
def sample_resources(model):
    stats = {"samples": 0, "peak_model_bytes": None, "minimum_memory_free_percent": None}
    stop = Event()

    def sample():
        while not stop.is_set():
            try:
                models = ollama_json("/api/ps")["models"]
                size = next((entry["size"] for entry in models if entry["name"] == model), None)
                if size is not None:
                    stats["peak_model_bytes"] = max(stats["peak_model_bytes"] or 0, size)
                free = memory_free_percent()
                if free is not None:
                    previous = stats["minimum_memory_free_percent"]
                    stats["minimum_memory_free_percent"] = free if previous is None else min(previous, free)
                stats["samples"] += 1
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                pass
            stop.wait(2)

    thread = Thread(target=sample, daemon=True)
    if model:
        thread.start()
    try:
        yield stats
    finally:
        stop.set()
        if model:
            thread.join()


def replay_model(case, pause_confirmed):
    """Script actions to exercise Pipkin and the graders independently of model quality."""
    order = sorted(case.outputs, key=lambda path: path.endswith(".html")) or [case.decline]
    steps = []
    for path in order:
        content = case.outputs[path].example if path in case.outputs else "New workshop\n"
        steps.append({"tool_calls": [{"function": {
            "name": "write_file", "arguments": {"path": path, "content": content},
        }}]})
    steps.append({"content": "Finished the requested steps."})
    replies = iter(steps)
    sent_first_step = False

    def step(_handler, messages, _tools, _think, _check, emit, **_options):
        nonlocal sent_first_step
        emit("step")
        if _options.get("output_schema") is not None:
            if not case.attachment:
                return {"content": json.dumps({"route": "agent"})}
            return {"content": json.dumps({
                "route": "render", "path": "index.html", "title": "Circuit Workshop", "intro": "",
                "sections": [
                    {"heading": "Meeting details", "items": ["Thursday at 4:00 p.m."], "sources": ["S1"]},
                    {"heading": "Preparation", "items": ["Bring a notebook", "Charge your calculator"], "sources": ["S2"]},
                ],
            })}
        if case.pause_after and sent_first_step and "Continue the same task from the saved plan" not in messages[0]["content"]:
            # Give the event reader time to send Stop if Work already started its next model step.
            if not pause_confirmed.wait(5):
                raise TimeoutError("The replay did not receive Stop.")
            return {}
        sent_first_step = True
        return next(replies, {"content": "Completed."})

    return step


def run_case(name, case, model, context, *, staged_site=False, explore_model=None):
    started = time.monotonic()
    with TemporaryDirectory(prefix="pipkin-benchmark-") as directory, ExitStack() as stack:
        root = Path(directory)
        work = root / "workspace"
        work.mkdir()
        for path, contents in case.files.items():
            (work / path).write_bytes(contents)
        workspace = server.Workspace(work)
        sessions = server.BrowserSessions()
        pause_confirmed = Event()
        for attr, value in {
            "CURRENT": {"model": model or "replay", "num_ctx": context},
            "SETTINGS_FILE": root / "settings.json",
            "SESSIONS": sessions,
            "WORKSPACES": {},
        }.items():
            stack.enter_context(patch.object(server, attr, value))
        if model:
            if ollama_json("/api/ps")["models"]:
                raise RuntimeError("Ollama already has a resident model. Start the benchmark when it is idle.")
            free = memory_free_percent()
            if free is not None and free < 65:
                raise RuntimeError(f"macOS reports {free}% memory free. Run the benchmark with more headroom.")
            def unload_model():
                if not sessions.unload(timeout=15):
                    raise RuntimeError("Ollama did not confirm that it unloaded the benchmark model.")

            stack.callback(unload_model)
        else:
            stack.enter_context(patch.object(sessions, "ensure_loaded"))
            stack.enter_context(patch.object(server.ChatHandler, "agent_step", replay_model(case, pause_confirmed)))
        resources = stack.enter_context(sample_resources(model))
        task = case.task
        attachments = []
        if case.attachment:
            data = base64.b64encode(case.files[case.attachment]).decode()
            doc = post("/api/document", {"kind": "pdf", "data": data})
            if doc["truncated"]:
                raise RuntimeError("The benchmark PDF was truncated.")
            attachments.append({"name": case.attachment, **doc})
            line_count = len(doc["text"].splitlines())
            task += f'\n\nAttached document "{case.attachment}"\n\n[A1] Extracted lines 1–{line_count}\n{doc["text"]}'
        key = server.grant_workspace(workspace)
        checkpoint = str(uuid4())
        options = {"task": task, "workspace": key, "mode": "ask", "workflow": "site" if case.site else None,
                   "web": False, "allowMemory": False, "think": False, "maxSteps": 10,
                   "exploreModel": explore_model}
        if staged_site and case.site:
            options["artifact"] = {"brief": case.task, "attachments": attachments}
        result = {"case": name, "first_saved_output_seconds": None, "first_verified_output_seconds": None,
                  "model_steps": 0, "explore_steps": 0, "write_steps": 0, "failed_tools": 0,
                  "approvals": 0, "unexpected_approvals": 0, "denied_requested_write": False,
                  "saved_paths": [], "restored_paths": [], "agent_timings_seconds": {}, "end_states": []}

        def run_phase(resume=False):
            phase = {"resume" if resume else "checkpoint": checkpoint}
            stop_sent = False
            allowed = set(case.outputs)
            if case.pause_after and not resume:
                allowed = {case.pause_after}
            with request("/api/agent/run", {**options, **phase}, timeout=150) as response:
                for line in response:
                    event = json.loads(line)
                    kind = event["event"]
                    if kind == "run":
                        run_id = event["id"]
                    elif kind == "approval":
                        result["approvals"] += 1
                        action, _, path = event["title"].partition(" ")
                        path = path.removeprefix("./")
                        edit = event["kind"] == "edit" and action in {"Create", "Edit"}
                        denied = edit and path == case.decline
                        approved = edit and path in allowed and not stop_sent
                        if not (edit and path in case.outputs) and not denied:
                            result["unexpected_approvals"] += 1
                        if denied:
                            result["denied_requested_write"] = True
                        if not stop_sent:
                            post("/api/agent/approve", {"run": run_id, "id": event["id"], "approved": approved})
                    elif kind == "model_step":
                        result["model_steps"] += 1
                        if event.get("phase") == "explore":
                            result["explore_steps"] += 1
                        else:
                            result["write_steps"] += 1
                    elif kind == "tool_result":
                        result["failed_tools"] += event["ok"] is False
                        path = event.get("saved_file")
                        if path is not None:
                            path = path.removeprefix("./")
                            result["saved_paths"].append(path)
                            if result["first_saved_output_seconds"] is None:
                                result["first_saved_output_seconds"] = round(time.monotonic() - started, 3)
                            if result["first_verified_output_seconds"] is None and any(
                                case.outputs[saved].passes(workspace, saved)
                                for saved in result["saved_paths"] if saved in case.outputs
                            ):
                                result["first_verified_output_seconds"] = round(time.monotonic() - started, 3)
                            if path == case.pause_after and not resume and not stop_sent:
                                post("/api/agent/stop", {"run": run_id})
                                stop_sent = True
                                pause_confirmed.set()
                    elif kind == "checkpoint_restored":
                        result["restored_paths"] = event["files"]
                    elif kind in {"stopped", "error", "result"}:
                        result["end_states"].append(kind)
                    elif kind == "usage":
                        for bucket, seconds in event.get("timings", {}).items():
                            timings = result["agent_timings_seconds"]
                            timings[bucket] = round(timings.get(bucket, 0) + seconds, 3)
            return stop_sent

        stopped = run_phase()
        if case.pause_after and stopped and result["end_states"] == ["stopped"]:
            saved = result["saved_paths"].copy()
            first_path = work / case.pause_after
            before = first_path.read_bytes()
            run_phase(resume=True)
            result["resume_preserved_first_output"] = (
                saved == result["restored_paths"] == [case.pause_after] and first_path.read_bytes() == before
            )
        expected = set(case.files) | set(case.outputs)
        files = {path.relative_to(work).as_posix() for path in work.rglob("*") if path.is_file()}
        result["output_checks"] = {path: output.checks(workspace, path) for path, output in case.outputs.items()}
        result["checks"] = {
            "expected_workspace_files": files == expected,
            "inputs_unchanged": all(path in files and (work / path).read_bytes() == contents
                                    for path, contents in case.files.items() if path not in case.outputs),
            "approved_scope_only": result["unexpected_approvals"] == 0,
            "completed": result["end_states"] == (["stopped", "result"] if case.pause_after else ["result"]),
            **{path: all(checks.values()) for path, checks in result["output_checks"].items()},
        }
        if case.pause_after:
            result["checks"]["resumed_from_saved_output"] = result.get("resume_preserved_first_output", False)
        if case.decline:
            result["checks"]["respected_decline"] = result["denied_requested_write"] and case.decline not in files
        result["passed"] = all(result["checks"].values())
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        result["resources"] = resources.copy()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=CASES, help="Select one or more tasks. Default is all six.")
    parser.add_argument("--model", help="Use this installed local Ollama model. Omit for scripted replay.")
    parser.add_argument("--explore-model", help="Optional smaller Ollama model for two-model Explore → Write runs.")
    parser.add_argument("--context", type=int, choices=server.CONTEXT_SIZES, default=16384)
    parser.add_argument("--repeat", type=int, choices=range(1, 21), default=1, metavar="1–20")
    parser.add_argument("--staged-site", action="store_true", help="Use the staged /site workflow used by the app.")
    parser.add_argument("--report", type=Path, help="Write a machine-readable JSON report.")
    args = parser.parse_args()
    model = None
    results = []
    interrupted = False
    try:
        if args.model:
            installed = server.installed_models()
            model = next((item for item in installed if item["name"] == args.model), None)
            if not model or not model.get("size") or args.model.endswith(":cloud"):
                raise RuntimeError("Choose the exact name of a downloaded local model shown by 'ollama list'.")
            if args.explore_model:
                ex = next((item for item in installed if item["name"] == args.explore_model), None)
                if not ex or not ex.get("size") or args.explore_model.endswith(":cloud"):
                    raise RuntimeError("Choose an installed local model for --explore-model.")
        for iteration in range(1, args.repeat + 1):
            for name in args.case or CASES:
                trial = run_case(name, CASES[name], args.model, args.context,
                                 staged_site=args.staged_site, explore_model=args.explore_model)
                trial["iteration"] = iteration
                results.append(trial)
                status = "PASS" if trial["passed"] else "FAIL"
                print(f"{status} {name} ({trial['wall_seconds']:.1f}s)", flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Benchmark stopped: {error}", file=sys.stderr)
        interrupted = True

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        metadata = {key: model.get(key) for key in ("name", "digest", "details")} if model else None
        payload = {"schema": 1, "mode": "ollama" if model else "replay", "model": metadata,
                   "explore_model": args.explore_model,
                   "platform": platform.system(), "architecture": platform.machine(),
                   "context": args.context, "think": False, "interrupted": interrupted,
                   "staged_site": args.staged_site, "trials": results}
        args.report.write_text(json.dumps(payload, indent=2) + "\n")
    if interrupted:
        return 2
    return 0 if all(trial["passed"] for trial in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
