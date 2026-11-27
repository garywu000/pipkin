"""Reviewed GitHub repository workflows. All writes stay within the selected grant."""
import base64
import contextlib
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import time

from github_connector import (GitHubHTTPError, MAX_BINARY_UPLOAD, MAX_COMMIT_UPLOAD_TOTAL,
                              MAX_FILE, MAX_RELEASE_ASSET, branch_name, file_path,
                              identity, quote)


def text(value, limit, label, *, empty=False):
    if (not isinstance(value, str) or len(value.encode("utf-8")) > limit
            or "\x00" in value or (not empty and not value.strip())):
        raise ValueError(f"Provide {label} (up to {limit} UTF-8 bytes).")
    return value


def number(value):
    if isinstance(value, str):
        cleaned = value.strip().lstrip("#")
        if cleaned.isascii() and cleaned.isdigit():
            value = int(cleaned)
    if type(value) is not int or not 1 <= value <= 10**15:
        raise ValueError("Specify a valid GitHub resource number or ID.")
    return value


def flag(value):
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    if type(value) is not bool:
        raise ValueError("Specify true or false.")
    return value


def _coerce_file_list(value):
    """Accept a list of file objects, a single object, or a JSON-encoded list string from small local models."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                return value
    if isinstance(value, dict):
        return [value]
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("GitHub did not return a valid commit or object SHA.")
    return value


def obj(value):
    if not isinstance(value, dict):
        raise ValueError("Unexpected GitHub resource response.")
    return value


def absent(turn, path):
    try:
        turn._get(path)
    except GitHubHTTPError as error:
        if error.status == 404:
            return True
        raise
    return False


def branch(turn, name, writable=False):
    data = obj(turn._get("/branches/" + quote(name)))
    if data.get("name") != name:
        raise ValueError("GitHub returned a different branch.")
    if writable and (name == turn.grant["defaultBranch"] or data.get("protected") is not False):
        raise ValueError("Choose an unprotected non-default feature branch.")
    return sha(obj(data.get("commit")).get("sha"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reviewed(turn, target, label, snapshot, payload, prepare, perform, approve, check):
    """Record durable intent and revalidate the exact reviewed state before writing."""
    if turn.uncertain_write:
        raise ValueError("A previous GitHub write has an uncertain outcome. Inspect the remote before retrying.")
    if target in turn.workflow_attempts:
        raise ValueError("This turn already attempted this GitHub action.")
    turn.workflow_attempts.add(target)
    detail = json.dumps({"account": turn.grant["account"], "repository": turn.grant["repo"],
                         "action": label, "expectedState": snapshot, "payload": payload},
                        ensure_ascii=False, indent=2)
    if len(detail.encode("utf-8")) > 96000:
        raise ValueError("The exact proposal is too large for one review. Split the changes.")
    started = time.monotonic()
    if not approve("github", label, detail):
        return "The user declined this GitHub action. No change was made."
    if time.monotonic() - started > 300:
        raise ValueError("GitHub approval expired. Review the current state again.")
    check()
    with turn.connection._lock:
        turn._check()
        fresh_token = turn.connection._token_source()
        api = turn.connection._api_factory(fresh_token)
        actual = identity(api, turn.grant["repo"])
        if ((actual["accountId"], actual["repoId"], actual["defaultBranch"]) !=
                (turn.grant["accountId"], turn.grant["repoId"], turn.grant["defaultBranch"])
                or not actual["push"]):
            raise ValueError("GitHub access or default branch changed. Review again.")
        turn.api = api
        if prepare() != snapshot:
            raise ValueError("GitHub state changed since review. No write was attempted.")
        check()
        key = digest([turn.grant["repoId"], target])
        intent = digest([turn.grant["accountId"], key, snapshot, payload])
        turn.journal.parent.mkdir(parents=True, exist_ok=True)
        try:
            with contextlib.closing(sqlite3.connect(turn.journal)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS workflow_writes (digest TEXT PRIMARY KEY, target TEXT, state TEXT, receipt TEXT)")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS workflow_pending ON workflow_writes(target) WHERE state='unknown'")
                if db.execute("SELECT 1 FROM workflow_writes WHERE (target=? AND state='unknown') OR (digest=? AND state='succeeded')",
                              (key, intent)).fetchone():
                    raise ValueError("A previous attempt is recorded. Inspect GitHub before repeating this action.")
                if target.startswith("branch:") and db.execute("SELECT 1 FROM sqlite_master WHERE name='pending_targets'").fetchone():
                    for file in snapshot["changes"]:
                        legacy = hashlib.sha256(json.dumps([turn.grant["repoId"], payload["branch"], file["path"]]).encode()).hexdigest()
                        if db.execute("SELECT 1 FROM pending_targets WHERE target=?", (legacy,)).fetchone():
                            raise ValueError("A previous file commit has an uncertain outcome. Inspect the branch first.")
                db.execute("INSERT INTO workflow_writes VALUES (?, ?, 'unknown', NULL)", (intent, key))
            if os.name != "nt":
                turn.journal.chmod(0o600)
        except (OSError, sqlite3.Error):
            raise ValueError("Could not record GitHub write intent.") from None
        turn.uncertain_write = True
        check()
        # Leave intent pending on any ambiguous failure, including malformed success responses.
        try:
            turn._active_token = fresh_token
            receipt = perform()
        except GitHubHTTPError as error:
            # Definitive validation/authentication failures can be reviewed again in a
            # fresh turn. Timeouts, rate limits and server failures remain uncertain.
            if error.status in (400, 401, 403, 404, 405, 409, 422):
                with contextlib.closing(sqlite3.connect(turn.journal)) as db, db:
                    db.execute("DELETE FROM workflow_writes WHERE digest=?", (intent,))
                turn.uncertain_write = False
            raise
        finally:
            turn._active_token = None
        try:
            with contextlib.closing(sqlite3.connect(turn.journal)) as db, db:
                db.execute("UPDATE workflow_writes SET state='succeeded', receipt=? WHERE digest=?", (receipt, intent))
        except sqlite3.Error:
            raise ValueError("GitHub returned success, but recording the receipt failed. Inspect the remote: " + receipt) from None
        turn.uncertain_write = False
        return "GitHub action succeeded: " + receipt


def write(turn, path, method, payload):
    return obj(turn.api.request(turn.root + path, method=method, body=payload))


def resource_receipt(turn, result, kind):
    return turn.url + "/" + kind + "/" + str(number(result.get("number")))


def review(turn, n):
    pr = obj(turn._get(f"/pulls/{n}"))
    head = sha(obj(pr.get("head")).get("sha"))
    base = sha(obj(pr.get("base")).get("sha"))
    count = pr.get("changed_files")
    if type(count) is not int or not 1 <= count <= 30:
        raise ValueError("Complete PR review supports 1–30 changed files.")
    files = turn._get(f"/pulls/{n}/files?per_page=100")
    if not isinstance(files, list) or len(files) != count:
        raise ValueError("Could not retrieve the complete PR file list.")
    patches = []
    for file in files:
        file = obj(file)
        patch = file.get("patch")
        if not isinstance(patch, str) or not patch:
            raise ValueError("PR includes a binary or unavailable patch; complete review is unavailable.")
        # GitHub may omit/truncate patches. Compare actual patch line totals.
        lines = patch.splitlines()
        if (sum(line.startswith("+") for line in lines) != file.get("additions")
                or sum(line.startswith("-") for line in lines) != file.get("deletions")):
            raise ValueError("A PR patch is incomplete; merge review cannot continue.")
        patches.append({k: file.get(k) for k in ("filename", "previous_filename", "status", "patch")})
    checks = obj(turn._get(f"/commits/{head}/check-runs?filter=latest&per_page=100"))
    runs = checks.get("check_runs")
    status = obj(turn._get(f"/commits/{head}/status?per_page=100"))
    statuses = status.get("statuses")
    if (not isinstance(runs, list) or checks.get("total_count") != len(runs)
            or not isinstance(statuses, list) or status.get("total_count") != len(statuses)):
        raise ValueError("Could not retrieve complete CI checks and commit statuses.")
    ci = [{"name": obj(run).get("name"), "status": run.get("status"),
           "conclusion": run.get("conclusion"), "id": run.get("id")} for run in runs]
    contexts = [{"context": obj(s).get("context"), "state": s.get("state")} for s in statuses]
    passed = (all(r["status"] == "completed" and r["conclusion"] in ("success", "neutral", "skipped") for r in ci)
              and all(s["state"] == "success" for s in contexts)
              and (not contexts or status.get("state") == "success"))
    final = obj(turn._get(f"/pulls/{n}"))
    if obj(final.get("head")).get("sha") != head or obj(final.get("base")).get("sha") != base:
        raise ValueError("PR changed during review.")
    return {"number": n, "head": head, "base": base, "state": final.get("state"),
            "draft": final.get("draft"), "mergeable": final.get("mergeable"),
            "checks": ci, "statuses": contexts, "checksPassed": passed,
            "checksConfigured": bool(ci or contexts), "files": patches}


def _git_blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()


_INLINE_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")


def _read_workspace_bytes(workspace, source, max_bytes, *, allow_empty=True):
    from agent import FILE_FLAGS, PEM_KEY, validate_path
    source = file_path(source)
    if "/".join(validate_path(source)) != source:
        raise ValueError("Use an exact relative source path in the selected workspace.")
    try:
        with workspace.open_path(source, FILE_FLAGS) as fd:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > max_bytes:
                raise ValueError(f"Only regular unlinked files up to {max_bytes // (1024 * 1024) or 1} MB may be uploaded.")
            if not allow_empty and before.st_size == 0:
                raise ValueError("Cannot upload an empty file as a release asset.")
            chunks = []
            remaining = max_bytes + 1
            with os.fdopen(os.dup(fd), "rb") as stream:
                while remaining > 0:
                    piece = stream.read(min(262_144, remaining))
                    if not piece:
                        break
                    chunks.append(piece)
                    remaining -= len(piece)
            raw = b"".join(chunks)
            after = os.fstat(fd)
    except OSError:
        raise ValueError("Cannot read the selected workspace file.") from None
    if (len(raw) != before.st_size or len(raw) > max_bytes or before.st_mtime_ns != after.st_mtime_ns
            or before.st_size != after.st_size):
        raise ValueError("Local file changed while reading.")
    sample = raw[:65536].decode("latin-1")
    if PEM_KEY.search(sample) or _INLINE_PEM.search(sample):
        raise ValueError("Private keys cannot be uploaded.")
    return source, raw


def local_files(turn, files):
    workspace = turn.workspace_resolver()
    if workspace is None:
        raise ValueError("Choose the workspace again before uploading local files.")
    files = _coerce_file_list(files)
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise ValueError("Name 1–8 exact local file paths and destinations.")
    edits, sources, blobs = [], [], {}
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"source", "path"}:
            raise ValueError("Each upload needs a source and destination path.")
        source, raw = _read_workspace_bytes(workspace, item["source"], MAX_BINARY_UPLOAD, allow_empty=True)
        total_bytes += len(raw)
        if total_bytes > MAX_COMMIT_UPLOAD_TOTAL:
            raise ValueError("Total upload size exceeds the 5 MB per-commit limit.")
        destination = file_path(item["path"])
        sha256 = hashlib.sha256(raw).hexdigest()
        git_sha = _git_blob_sha(raw)
        is_text = b"\x00" not in raw and len(raw) <= MAX_FILE
        text_content = None
        if is_text:
            try:
                text_content = raw.decode("utf-8")
            except UnicodeError:
                is_text = False
        if is_text:
            edits.append({"path": destination, "content": text_content})
            sources.append({"workspace": workspace.path.name, "source": source, "path": destination,
                            "encoding": "utf-8", "sha256": sha256, "bytes": len(raw)})
        else:
            edits.append({"path": destination, "content": None, "binary": True, "bytes": len(raw),
                          "sha256": sha256, "gitBlobSha": git_sha})
            blobs[destination] = base64.b64encode(raw).decode("ascii")
            sources.append({"workspace": workspace.path.name, "source": source, "path": destination,
                            "encoding": "binary", "sha256": sha256, "bytes": len(raw)})
    return edits, sources, blobs


def commit_files(turn, args, approve, check, local=False):
    name = branch_name(args.get("branch"))
    message = text(args.get("message"), 200, "commit message")
    original_files = args.get("files")
    files, sources, blobs = local_files(turn, original_files) if local else (_coerce_file_list(original_files), None, {})
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise ValueError("Provide 1–8 file edits.")
    edits = {}
    for file in files:
        if local and isinstance(file, dict) and file.get("binary"):
            path = file_path(file["path"])
            if path in edits or path.lower().startswith(".github/workflows/"):
                raise ValueError("Duplicate paths and workflow changes are not supported.")
            edits[path] = {"binary": True, "bytes": file["bytes"], "sha256": file["sha256"], "gitBlobSha": file["gitBlobSha"]}
            continue
        if not isinstance(file, dict) or set(file) != {"path", "content"}:
            raise ValueError("Each edit requires path and content (null to delete).")
        path = file_path(file["path"])
        if path in edits or path.lower().startswith(".github/workflows/"):
            raise ValueError("Duplicate paths and workflow changes are not supported.")
        content = file["content"]
        edits[path] = None if content is None else text(content, MAX_FILE, "file content", empty=True)

    def prepare():
        if local and local_files(turn, original_files) != (files, sources, blobs):
            raise ValueError("Local upload sources changed since review.")
        head = branch(turn, name, writable=True)
        commit = obj(turn._get("/git/commits/" + head))
        tree = sha(obj(commit.get("tree")).get("sha"))
        listing = obj(turn._get("/git/trees/" + tree + "?recursive=1"))
        entries = listing.get("tree")
        if listing.get("truncated") is not False or not isinstance(entries, list):
            raise ValueError("Cannot verify the full destination tree.")
        by_path = {obj(entry).get("path"): entry for entry in entries}
        changes = []
        for path, content in edits.items():
            entry = by_path.get(path)
            if entry and (entry.get("type") != "blob" or entry.get("mode") not in ("100644", "100755")):
                raise ValueError("Only regular files may be edited.")
            parts = path.split("/")
            for i in range(1, len(parts)):
                parent = by_path.get("/".join(parts[:i]))
                if parent and parent.get("type") != "tree":
                    raise ValueError("A destination ancestor is not a directory.")
            if any(other.startswith(path + "/") or path.startswith(other + "/") for other in edits if other != path):
                raise ValueError("Conflicting file paths in the same commit.")
            if isinstance(content, dict) and content.get("binary"):
                prev_sha = sha(entry["sha"]) if entry else None
                if prev_sha == content["gitBlobSha"]:
                    continue
                changes.append({"path": path, "mode": entry["mode"] if entry else "100644",
                                "encoding": "binary", "bytes": content["bytes"], "sha256": content["sha256"],
                                "previousBlob": prev_sha, "new": None})
                continue
            old = turn._text(turn._content(path, head)) if entry else None
            if entry and old is None:
                raise ValueError("Could not read the original file.")
            if old == content:
                continue
            changes.append({"path": path, "mode": entry["mode"] if entry else "100644",
                            "old": old, "previousBlob": sha(entry["sha"]) if entry else None, "new": content})
        return {"head": head, "tree": tree, "changes": changes, "sources": sources}

    snapshot = prepare()
    if not snapshot["changes"]:
        return "All requested files already match. No commit was made."

    def perform():
        entries = []
        for f in snapshot["changes"]:
            if f.get("encoding") == "binary":
                blob_resp = write(turn, "/git/blobs", "POST", {"content": blobs[f["path"]], "encoding": "base64"})
                blob_sha = sha(blob_resp.get("sha"))
                entries.append({"path": f["path"], "mode": f["mode"], "type": "blob", "sha": blob_sha})
            else:
                entries.append({"path": f["path"], "mode": f["mode"], "type": "blob",
                                **({"sha": None} if f["new"] is None else {"content": f["new"]})})
        tree = sha(write(turn, "/git/trees", "POST", {"base_tree": snapshot["tree"], "tree": entries}).get("sha"))
        commit = sha(write(turn, "/git/commits", "POST", {"message": message, "tree": tree,
                                                         "parents": [snapshot["head"]]}).get("sha"))
        check()
        if branch(turn, name, writable=True) != snapshot["head"]:
            raise ValueError("Branch changed while preparing the commit. Its ref was not updated.")
        ref = write(turn, "/git/refs/heads/" + quote(name), "PATCH", {"sha": commit, "force": False})
        if sha(obj(ref.get("object")).get("sha")) != commit:
            raise ValueError("GitHub returned an uncertain branch update.")
        return turn.url + "/commit/" + commit

    return reviewed(turn, "branch:" + name, "Commit files to " + name, snapshot,
                    {"branch": name, "message": message, "files": files}, prepare, perform, approve, check)


def _asset_filename(value):
    if (not isinstance(value, str) or not 0 < len(value.strip()) <= 200
            or "/" in value or "\\" in value or value.strip() in (".", "..")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Provide a valid release asset filename.")
    return value.strip()


def upload_release_asset(turn, args, approve, check):
    workspace = turn.workspace_resolver()
    if workspace is None:
        raise ValueError("Choose the workspace again before uploading a release asset.")
    release_id = number(args.get("id"))
    raw_source = args.get("source")
    source, initial_raw = _read_workspace_bytes(workspace, raw_source, MAX_RELEASE_ASSET, allow_empty=False)
    asset_name = _asset_filename(args.get("name") if args.get("name") is not None else Path(source).name)
    label_val = args.get("label")
    asset_label = text(label_val, 120, "asset label", empty=True).strip() if label_val is not None else ""
    content_type = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
    expected_sha = hashlib.sha256(initial_raw).hexdigest()
    expected_size = len(initial_raw)
    del initial_raw  # Do not retain large binaries while awaiting approval.
    upload_holder = {}

    def prepare():
        ws = turn.workspace_resolver()
        if ws is None:
            raise ValueError("Choose the workspace again before uploading a release asset.")
        _, raw_now = _read_workspace_bytes(ws, raw_source, MAX_RELEASE_ASSET, allow_empty=False)
        if len(raw_now) != expected_size or hashlib.sha256(raw_now).hexdigest() != expected_sha:
            raise ValueError("Local release asset changed since review.")
        release = obj(turn._get(f"/releases/{release_id}"))
        if release.get("id") != release_id:
            raise ValueError("Select an existing GitHub release.")
        upload_url = release.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            raise ValueError("GitHub release did not provide an upload URL.")
        existing_assets = release.get("assets") or []
        if any(isinstance(a, dict) and str(a.get("name", "")).casefold() == asset_name.casefold() for a in existing_assets):
            raise ValueError(f"Release already has an asset named {asset_name}.")
        upload_holder["url"] = upload_url
        return {
            "releaseId": release_id,
            "tag": str(release.get("tag_name") or ""),
            "releaseName": str(release.get("name") or ""),
            "draft": bool(release.get("draft")),
            "assetName": asset_name,
            "label": asset_label,
            "source": source,
            "workspace": ws.path.name,
            "bytes": expected_size,
            "sha256": expected_sha,
            "contentType": content_type,
        }

    snapshot = prepare()
    payload = {"releaseId": release_id, "source": source, "name": asset_name, "contentType": content_type,
               **({"label": asset_label} if asset_label else {})}

    def perform():
        ws = turn.workspace_resolver()
        if ws is None:
            raise ValueError("Workspace access expired before uploading asset.")
        _, raw_final = _read_workspace_bytes(ws, raw_source, MAX_RELEASE_ASSET, allow_empty=False)
        if len(raw_final) != expected_size or hashlib.sha256(raw_final).hexdigest() != expected_sha:
            raise ValueError("Local release asset changed right before upload.")
        result = obj(turn.api.upload_asset(upload_holder["url"], asset_name, content_type, raw_final,
                                           label=asset_label or None))
        number(result.get("id"))
        if result.get("name") != asset_name:
            raise ValueError("GitHub returned an unexpected release asset name.")
        return str(result.get("browser_download_url") or (turn.url + "/releases"))

    return reviewed(turn, f"release-asset:{release_id}:{asset_name}",
                    f"Upload asset {asset_name} to release {snapshot['tag'] or f'#{release_id}'}",
                    snapshot, payload, prepare, perform, approve, check)


def _parse_github_remote(url):
    if not isinstance(url, str):
        return None
    url = url.strip()
    match = re.fullmatch(
        r"(?:https://(?:[^/@\s]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)([A-Za-z0-9-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?",
        url, re.I,
    )
    return match.group(1) if match else None


def _default_git_runner(cwd, git_args, *, extra_env=None, timeout=30):
    git = shutil.which("git")
    if not git:
        raise ValueError("Git is not installed or not on PATH.")
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"GH_TOKEN", "GITHUB_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS",
                                "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_TRACE", "GIT_CURL_VERBOSE"}}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra_env:
        env.update(extra_env)
    # A folder's own .git/config could name programs that git runs (fsmonitor, hooks, external diff and
    # textconv drivers). Switch those off for every command Pipkin runs, so a downloaded repository can't
    # make Pipkin execute anything just by being inspected or pushed.
    hardened = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, "-c", "diff.external=",
                "-c", "core.pager=cat"]
    if git_args and git_args[0] in ("diff", "show", "log"):
        git_args = [git_args[0], "--no-ext-diff", "--no-textconv", *git_args[1:]]
    try:
        result = subprocess.run(
            [git, *hardened, *git_args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Local Git operation failed or timed out.") from None
    return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")


def push_branch(turn, args, approve, check):
    from agent import PEM_KEY
    workspace = turn.workspace_resolver()
    if workspace is None:
        raise ValueError("Choose the workspace again before pushing local Git commits.")
    run_git = turn.git_runner or _default_git_runner
    root_path = workspace.path.resolve()

    def git_ok(*git_args, error_msg="Local Git command failed.", extra_env=None, timeout=30):
        code, out, _err = run_git(root_path, list(git_args), extra_env=extra_env, timeout=timeout)
        if code != 0:
            raise ValueError(error_msg)
        return out

    # Resolve local branch name once up front.
    raw_local = args.get("local_branch")
    if raw_local is None or (isinstance(raw_local, str) and not raw_local.strip()):
        head_name = git_ok("symbolic-ref", "--short", "HEAD",
                           error_msg="Workspace is in detached HEAD state. Specify local_branch or check out a branch.").strip()
        local_branch = branch_name(head_name)
    else:
        local_branch = branch_name(raw_local)
    raw_remote = args.get("remote_branch")
    remote_branch = branch_name(local_branch if (raw_remote is None or (isinstance(raw_remote, str) and not raw_remote.strip())) else raw_remote)
    if remote_branch == turn.grant["defaultBranch"]:
        raise ValueError("Refusing to push directly to the repository's default branch. Push to a feature branch and open a PR.")

    def prepare():
        ws = turn.workspace_resolver()
        if ws is None or ws.path.resolve() != root_path:
            raise ValueError("Workspace changed since review.")
        toplevel = git_ok("rev-parse", "--show-toplevel", error_msg="Selected workspace is not a Git repository.").strip()
        if Path(toplevel).resolve() != root_path:
            raise ValueError("Select the Git repository's root folder to push local commit history.")
        origin_url = git_ok("remote", "get-url", "origin", error_msg="Local Git repository has no 'origin' remote.").strip()
        origin_repo = _parse_github_remote(origin_url)
        if not origin_repo or origin_repo.casefold() != turn.grant["repo"].casefold():
            raise ValueError(f"Workspace 'origin' remote does not match connected repository {turn.grant['repo']}.")
        local_sha = sha(git_ok("rev-parse", "--verify", "refs/heads/" + local_branch,
                               error_msg=f"Local branch '{local_branch}' does not exist.").strip())
        remote_exists = not absent(turn, "/git/ref/heads/" + quote(remote_branch))
        remote_sha = branch(turn, remote_branch, writable=True) if remote_exists else None
        if remote_sha == local_sha:
            return {"upToDate": True, "localBranch": local_branch, "remoteBranch": remote_branch, "localSha": local_sha, "remoteSha": remote_sha}
        if remote_sha:
            code, _, _ = run_git(root_path, ["merge-base", "--is-ancestor", remote_sha, local_sha])
            if code != 0:
                raise ValueError(f"Push to '{remote_branch}' is not a fast-forward (force-push is disabled). Pull or rebase locally first.")
            rev_range = f"{remote_sha}..{local_sha}"
        else:
            default_sha = branch(turn, turn.grant["defaultBranch"])
            code, _, _ = run_git(root_path, ["merge-base", "--is-ancestor", default_sha, local_sha])
            rev_range = f"{default_sha}..{local_sha}" if code == 0 else local_sha
        count_raw = git_ok("rev-list", "--count", rev_range, error_msg="Could not inspect local commit history.").strip()
        commit_count = int(count_raw) if count_raw.isdigit() else 0
        if commit_count <= 0:
            raise ValueError("No new local commits found to push.")
        if commit_count > 100:
            raise ValueError("Pushing more than 100 commits in one review is not supported. Push in smaller batches.")
        log_out = git_ok("log", "-n", "30", "--format=%H%x1f%s%x1f%an%x1f%ad", "--date=short", rev_range,
                         error_msg="Could not read local commit log.")
        commits = []
        for line in log_out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({"sha": parts[0][:12], "subject": parts[1][:140], "author": parts[2][:60], "date": parts[3]})
        diff_base = remote_sha if remote_sha else (rev_range.split("..")[0] if ".." in rev_range else None)
        if diff_base:
            files_out = git_ok("diff", "--name-status", f"{diff_base}..{local_sha}", error_msg="Could not inspect changed files.")
            patch_out = git_ok("diff", f"{diff_base}..{local_sha}", error_msg="Could not inspect commit diff.")
        else:
            files_out = git_ok("show", "--name-status", "--format=", local_sha, error_msg="Could not inspect changed files.")
            patch_out = git_ok("show", "--format=", local_sha, error_msg="Could not inspect commit diff.")
        changed_files = []
        for line in files_out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status_code = parts[0]
                paths = parts[1:]
                if any(p.lower().startswith(".github/workflows/") for p in paths):
                    raise ValueError("Commits modifying .github/workflows/ cannot be pushed through this connector.")
                changed_files.append({"status": status_code, "path": paths[-1]})
        if any(line.startswith("+") and not line.startswith("+++") and PEM_KEY.search(line) for line in patch_out.splitlines()):
            raise ValueError("Private keys detected in commit diff; push refused.")
        dirty_code, dirty_out, _ = run_git(root_path, ["status", "--porcelain"])
        return {
            "upToDate": False,
            "workspace": ws.path.name,
            "localBranch": local_branch,
            "remoteBranch": remote_branch,
            "localSha": local_sha,
            "remoteSha": remote_sha,
            "newBranch": not remote_exists,
            "commitCount": commit_count,
            "commits": commits,
            "fileCount": len(changed_files),
            "files": changed_files[:40],
            "workingTreeDirty": dirty_code == 0 and bool(dirty_out.strip()),
        }

    snapshot = prepare()
    if snapshot.get("upToDate"):
        return f"Remote branch {remote_branch} is already up to date at {snapshot['localSha'][:12]}. No push was needed."
    payload = {"localBranch": local_branch, "remoteBranch": remote_branch,
               "localSha": snapshot["localSha"], "remoteSha": snapshot["remoteSha"], "force": False}

    def perform():
        token = getattr(turn, "_active_token", None)
        if not isinstance(token, str) or not token:
            raise ValueError("Missing fresh GitHub credential for push.")
        auth_b64 = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        push_env = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + auth_b64,
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": "",
        }
        remote_url = f"https://github.com/{turn.grant['repo']}.git"
        refspec = f"{snapshot['localSha']}:refs/heads/{remote_branch}"
        code, _out, _err = run_git(root_path, ["push", "--no-verify", "--porcelain", remote_url, refspec],
                                   extra_env=push_env, timeout=120)
        if code != 0:
            raise ValueError("Git push failed. Check branch rules, network access, and remote state before retrying.")
        updated_sha = branch(turn, remote_branch, writable=True)
        if updated_sha != snapshot["localSha"]:
            raise ValueError("Remote branch SHA does not match pushed commit. Inspect GitHub before retrying.")
        return turn.url + "/tree/" + quote(remote_branch)

    return reviewed(turn, "push:" + remote_branch,
                    f"Push {snapshot['commitCount']} local {'commit' if snapshot['commitCount'] == 1 else 'commits'} to {remote_branch}",
                    snapshot, payload, prepare, perform, approve, check)


def execute(turn, name, args, approve, check):
    if name == "github_review_pr":
        n = number(args.get("number"))
        snapshot = review(turn, n)
        if len(turn._result(snapshot, turn.url + f"/pull/{n}/files")) > 14000 or len(json.dumps(snapshot, ensure_ascii=False, indent=2)) > 14000:
            raise ValueError("Complete PR diff exceeds the review limit. Review manually or split the PR.")
        turn.reviewed_prs[n] = snapshot
        return turn._result(snapshot, turn.url + f"/pull/{n}/files")
    if name == "github_releases":
        releases = turn._get("/releases?per_page=15")
        if not isinstance(releases, list):
            raise ValueError("Unexpected GitHub release response.")
        summary = []
        for r in releases:
            item = {k: obj(r).get(k) for k in ("id", "tag_name", "name", "draft", "prerelease", "html_url")}
            assets = r.get("assets")
            if isinstance(assets, list):
                item["assets"] = [{k: obj(a).get(k) for k in ("id", "name", "size")} for a in assets[:20]]
            summary.append(item)
        return turn._result(summary, turn.url + "/releases")
    if name in ("github_commit_files", "github_upload_files"):
        return commit_files(turn, args, approve, check, local=name == "github_upload_files")
    if name == "github_upload_release_asset":
        return upload_release_asset(turn, args, approve, check)
    if name == "github_push_branch":
        return push_branch(turn, args, approve, check)

    if name == "github_create_branch":
        new, base = branch_name(args.get("branch")), branch_name(args.get("base"))
        if new == turn.grant["defaultBranch"]:
            raise ValueError("Choose a feature branch.")
        def prepare():
            if not absent(turn, "/git/ref/heads/" + quote(new)):
                raise ValueError("Destination branch already exists.")
            return {"base": base, "sha": branch(turn, base)}
        snapshot = prepare()
        payload = {"ref": "refs/heads/" + new, "sha": snapshot["sha"]}
        def perform():
            result = write(turn, "/git/refs", "POST", payload)
            if result.get("ref") != payload["ref"] or sha(obj(result.get("object")).get("sha")) != payload["sha"]:
                raise ValueError("Uncertain branch creation outcome.")
            return turn.url + "/tree/" + quote(new)
        target, label = "create-branch:" + new, "Create branch " + new
    elif name == "github_create_pr":
        head, base = branch_name(args.get("head")), branch_name(args.get("base"))
        if head == base:
            raise ValueError("PR branches must differ.")
        raw_draft = args.get("draft")
        payload = {"head": head, "base": base, "title": text(args.get("title"), 256, "PR title"),
                   "body": text(args.get("body") if args.get("body") is not None else "", 12000, "PR body", empty=True),
                   "draft": flag(False if raw_draft is None else raw_draft)}
        def prepare():
            return {"head": branch(turn, head), "base": branch(turn, base)}
        snapshot = prepare()
        def perform():
            result = write(turn, "/pulls", "POST", payload)
            if obj(result.get("head")).get("sha") != snapshot["head"] or obj(result.get("base")).get("sha") != snapshot["base"]:
                raise ValueError("PR was created but the branches changed. Inspect the PR before continuing.")
            return resource_receipt(turn, result, "pull")
        target, label = "pr:" + head + ":" + base, "Open PR " + head + " → " + base
    elif name == "github_merge_pr":
        n = number(args.get("number"))
        method = args.get("method")
        if method not in ("merge", "squash", "rebase"):
            raise ValueError("Choose merge, squash or rebase.")
        snapshot = turn.reviewed_prs.get(n)
        if not snapshot:
            raise ValueError("Review the complete PR with github_review_pr first.")
        def prepare():
            return review(turn, n)
        if (snapshot["state"] != "open" or snapshot["draft"] is not False
                or snapshot["mergeable"] is not True or not snapshot["checksPassed"]):
            raise ValueError("PR is not open, mergeable, non-draft and passing all reported checks.")
        payload = {"sha": snapshot["head"], "merge_method": method}
        def perform():
            result = write(turn, f"/pulls/{n}/merge", "PUT", payload)
            if result.get("merged") is not True:
                raise ValueError("GitHub did not confirm the merge. Inspect the PR.")
            sha(result.get("sha"))
            return turn.url + f"/pull/{n}"
        target, label = f"merge:{n}", f"Merge reviewed PR #{n}"
    elif name == "github_create_issue":
        payload = {"title": text(args.get("title"), 256, "issue title"),
                   "body": text(args.get("body") if args.get("body") is not None else "", 12000, "issue body", empty=True)}
        def prepare():
            return {}
        snapshot = prepare()
        def perform():
            return resource_receipt(turn, write(turn, "/issues", "POST", payload), "issues")
        target, label = "create-issue:" + digest(payload), "Create issue"
    elif name in ("github_update_issue", "github_comment"):
        n = number(args.get("number"))
        def prepare():
            item = obj(turn._get(f"/issues/{n}"))
            if item.get("number") != n or (name == "github_update_issue" and "pull_request" in item):
                raise ValueError("Select the correct issue. Pull requests are separate resources.")
            return {k: item.get(k) for k in ("number", "title", "body", "state", "updated_at", "html_url")}
        snapshot = prepare()
        if name == "github_update_issue":
            payload = {k: text(args[k], limit, k, empty=k == "body")
                       for k, limit in (("title", 256), ("body", 12000)) if k in args}
            if "state" in args:
                if args["state"] not in ("open", "closed"):
                    raise ValueError("Choose open or closed.")
                payload["state"] = args["state"]
            if "state_reason" in args:
                allowed = ("completed", "not_planned") if payload.get("state") == "closed" else ("reopened",) if payload.get("state") == "open" else ()
                if args["state_reason"] not in allowed:
                    raise ValueError("State reason must match the requested issue state.")
                payload["state_reason"] = args["state_reason"]
            if not payload:
                raise ValueError("Provide an issue change.")
            def perform():
                return resource_receipt(turn, write(turn, f"/issues/{n}", "PATCH", payload), "issues")
            target, label = f"issue:{n}", f"Update issue #{n}"
        else:
            payload = {"body": text(args.get("body"), 12000, "comment")}
            def perform():
                result = write(turn, f"/issues/{n}/comments", "POST", payload)
                return turn.url + f"/issues/{n}#issuecomment-" + str(number(result.get("id")))
            target, label = f"comment:{n}:" + digest(payload), f"Comment on #{n}"
    elif name == "github_create_release":
        tag, source = branch_name(args.get("tag")), branch_name(args.get("branch"))
        def prepare():
            if not absent(turn, "/git/ref/tags/" + quote(tag)):
                raise ValueError("Tag already exists. Choose a new version tag.")
            if not absent(turn, "/releases/tags/" + quote(tag)):
                raise ValueError("Release already exists.")
            return {"targetCommit": branch(turn, source)}
        snapshot = prepare()
        raw_draft = args.get("draft")
        raw_pre = args.get("prerelease")
        payload = {"tag_name": tag, "target_commitish": snapshot["targetCommit"],
                   "name": text(args.get("name"), 256, "release name"),
                   "body": text(args.get("body") if args.get("body") is not None else "", 12000, "release notes", empty=True),
                   "draft": flag(True if raw_draft is None else raw_draft),
                   "prerelease": flag(False if raw_pre is None else raw_pre)}
        def perform():
            result = write(turn, "/releases", "POST", payload)
            if result.get("tag_name") != tag:
                raise ValueError("Uncertain release outcome.")
            rid = number(result.get("id"))
            url = turn.url + ("/releases/tag/" + quote(tag) if not payload["draft"] else "/releases")
            return f"{url} (release id {rid})"
        target, label = "release:" + tag, ("Create draft release " if payload["draft"] else "Publish release ") + tag
    elif name == "github_publish_release":
        release_id = number(args.get("id"))
        def prepare():
            release = obj(turn._get(f"/releases/{release_id}"))
            if release.get("id") != release_id or release.get("draft") is not True:
                raise ValueError("Select an existing draft release.")
            tag = branch_name(release.get("tag_name"))
            # Drafts may have a mutable branch target. Pin the resolved commit for review.
            ref = obj(turn._get("/commits/" + quote(branch_name(release.get("target_commitish")))))
            target_commit = sha(ref.get("sha"))
            tag_path = "/git/ref/tags/" + quote(tag)
            tag_ref = None if absent(turn, tag_path) else obj(turn._get(tag_path)).get("object")
            if tag_ref is not None:
                if obj(tag_ref).get("type") != "commit" or tag_ref.get("sha") != target_commit:
                    raise ValueError("Existing tag does not match the reviewed commit.")
            return {"tag": tag, "targetCommit": target_commit, "tagRef": tag_ref,
                    "name": release.get("name"), "body": release.get("body"),
                    "prerelease": release.get("prerelease"), "assets": release.get("assets")}
        snapshot = prepare()
        payload = {"draft": False, "target_commitish": snapshot["targetCommit"]}
        def perform():
            result = write(turn, f"/releases/{release_id}", "PATCH", payload)
            if result.get("id") != release_id or result.get("draft") is not False:
                raise ValueError("Uncertain release publication outcome.")
            return turn.url + "/releases/tag/" + quote(snapshot["tag"])
        target, label = "publish-release:" + snapshot["tag"], "Publish draft release " + snapshot["tag"]
    else:
        raise ValueError("Unknown GitHub workflow.")
    return reviewed(turn, target, label, snapshot, payload, prepare, perform, approve, check)

