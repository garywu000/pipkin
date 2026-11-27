"""Offline integration coverage for reviewed repository workflows."""
import base64
import hashlib
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

import github_connector as github
import github_workflows
from agent import Workspace
try:
    from tests.test_github_connector import Provider
except ImportError:
    from test_github_connector import Provider


class WorkflowProvider(Provider):
    def __init__(self):
        super().__init__()
        self.branches = {"main": "a" * 40}
        self.tree = "b" * 40
        self.pr_head = "d" * 40
        self.pr_base = "a" * 40
        self.check_state = "success"
        self.issue = {"number": 4, "title": "Bug", "body": "Fix it", "state": "open", "updated_at": "1"}
        self.release = None
        self.pr = None
        self.blobs = {}
        self.asset_uploads = []

    def api(self, token):
        fallback = super().api(token)
        parent = self
        class API:
            def request(self, path, *, method="GET", body=None):
                if path in ("/user", "/repos/octo/project"):
                    return fallback.request(path, method=method, body=body)
                parent.calls.append((token, method, path, body))
                path = path.removeprefix("/repos/octo/project")
                if method != "GET" and parent.fail_write:
                    raise ValueError("Unknown provider outcome")
                if path.startswith("/branches/"):
                    name = path.split("/")[-1]
                    if name not in parent.branches:
                        raise github.GitHubHTTPError(404, "GET")
                    return {"name": name, "protected": name == "main", "commit": {"sha": parent.branches[name]}}
                if path.startswith("/git/ref/heads/"):
                    name = path.split("/")[-1]
                    if name not in parent.branches:
                        raise github.GitHubHTTPError(404, "GET")
                    return {"object": {"sha": parent.branches[name]}}
                if path == "/git/refs" and method == "POST":
                    name = body["ref"].removeprefix("refs/heads/")
                    parent.branches[name] = body["sha"]
                    return {"ref": body["ref"], "object": {"sha": body["sha"]}}
                if path == "/git/blobs" and method == "POST":
                    raw = base64.b64decode(body["content"])
                    blob_sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                    parent.blobs[blob_sha] = raw
                    return {"sha": blob_sha}
                if path.startswith("/git/commits/"):
                    return {"tree": {"sha": parent.tree}}
                if path.startswith("/git/trees/"):
                    return {"truncated": False, "tree": [{"path": "README.md", "mode": "100644", "type": "blob", "sha": "e" * 40}]}
                if path.startswith("/contents/README.md?ref="):
                    return {"type": "file", "encoding": "base64", "size": 4, "content": base64.b64encode(b"old\n").decode()}
                if path == "/git/trees" and method == "POST":
                    return {"sha": "c" * 40}
                if path == "/git/commits" and method == "POST":
                    return {"sha": parent.pr_head}
                if path.startswith("/git/refs/heads/") and method == "PATCH":
                    parent.branches[path.split("/")[-1]] = body["sha"]
                    return {"object": {"sha": body["sha"]}}
                if path == "/pulls" and method == "POST":
                    parent.pr = {"number": 7, "state": "open", "draft": body["draft"], "mergeable": True, "changed_files": 1,
                                 "head": {"sha": parent.branches[body["head"]]}, "base": {"sha": parent.branches[body["base"]]}}
                    return parent.pr
                if path == "/pulls/7":
                    return dict(parent.pr)
                if path == "/pulls/7/files?per_page=100":
                    return [{"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new", "additions": 1, "deletions": 1}]
                if path.endswith("/check-runs?filter=latest&per_page=100"):
                    return {"total_count": 1, "check_runs": [{"id": 1, "name": "test", "status": "completed", "conclusion": parent.check_state}]}
                if path.endswith("/status?per_page=100"):
                    return {"total_count": 0, "statuses": [], "state": "pending"}
                if path == "/pulls/7/merge" and method == "PUT":
                    parent.pr["state"] = "closed"
                    return {"merged": True, "sha": "f" * 40}
                if path == "/issues" and method == "POST":
                    return {"number": 4}
                if path == "/issues/4":
                    if method == "PATCH":
                        parent.issue.update(body)
                        parent.issue["updated_at"] = "2"
                    return dict(parent.issue)
                if path == "/issues/4/comments" and method == "POST":
                    return {"id": 8}
                if path.startswith("/git/ref/tags/") or path.startswith("/releases/tags/"):
                    raise github.GitHubHTTPError(404, "GET")
                if path == "/releases" and method == "POST":
                    parent.release = {
                        "id": 9, **body, "assets": [],
                        "upload_url": "https://uploads.github.com/repos/octo/project/releases/9/assets{?name,label}",
                    }
                    return parent.release
                if path.startswith("/commits/"):
                    return {"sha": parent.branches["main"]}
                if path == "/releases/9":
                    if method == "PATCH":
                        parent.release.update(body)
                    return dict(parent.release)
                raise AssertionError(f"Unexpected {method} {path}")

            def upload_asset(self, upload_url, name, content_type, data, *, label=None):
                if parent.fail_write:
                    raise ValueError("Unknown provider outcome")
                parent.asset_uploads.append((token, upload_url, name, content_type, data, label))
                asset = {"id": 55, "name": name, "size": len(data),
                         "browser_download_url": f"https://github.com/octo/project/releases/download/v2.4.1/{name}"}
                parent.release["assets"].append(asset)
                return asset
        return API()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.journal = Path(temp.name) / "journal.sqlite3"
        self.provider = WorkflowProvider()
        self.connection = github.Connection(lambda: self.provider.token, self.provider.api)
        self.connection.connect("octo/project", True)
        self.approvals = []

    def turn(self, writes=True):
        return self.connection.turn(writes=writes, journal=self.journal)

    def approve(self, kind, title, detail):
        self.assertEqual(kind, "github")
        self.assertIn("octo/project", detail)
        self.approvals.append(title)
        return True

    def execute(self, turn, action, args):
        return turn.execute("github_" + action, args, approve=self.approve)

    def make_pr(self, turn):
        self.execute(turn, "create_branch", {"branch": "dev", "base": "main"})
        self.execute(turn, "commit_files", {"branch": "dev", "message": "Update files", "files": [
            {"path": "README.md", "content": "new\n"}, {"path": "docs/notes.txt", "content": "notes"}]})
        self.execute(turn, "create_pr", {"head": "dev", "base": "main", "title": "Fix", "body": "Fixes #4", "draft": False})

    def test_branch_commit_pr_review_merge(self):
        turn = self.turn()
        self.make_pr(turn)
        self.assertEqual(self.provider.branches["dev"], self.provider.pr_head)
        with self.assertRaisesRegex(ValueError, "Review"):
            self.execute(turn, "merge_pr", {"number": 7, "method": "squash"})
        self.assertIn("checksPassed", self.execute(turn, "review_pr", {"number": 7}))
        self.assertIn("/pull/7", self.execute(turn, "merge_pr", {"number": 7, "method": "squash"}))
        self.assertEqual(len(self.approvals), 4)
        ref = next(c for c in self.provider.calls if c[1] == "PATCH" and "/git/refs/" in c[2])
        self.assertFalse(ref[3]["force"])
        self.assertEqual(self.provider.pr["state"], "closed")

    def test_checks_and_changed_head_block_merge(self):
        turn = self.turn()
        self.make_pr(turn)
        self.provider.check_state = "failure"
        self.execute(turn, "review_pr", {"number": 7})
        with self.assertRaisesRegex(ValueError, "passing"):
            self.execute(turn, "merge_pr", {"number": 7, "method": "merge"})
        self.provider.check_state = "success"
        self.execute(turn, "review_pr", {"number": 7})
        self.provider.pr["head"]["sha"] = "9" * 40
        with self.assertRaisesRegex(ValueError, "changed"):
            self.execute(turn, "merge_pr", {"number": 7, "method": "merge"})
        self.assertFalse(any(c[2].endswith("/merge") for c in self.provider.calls))

    def test_issue_create_comment_close(self):
        turn = self.turn()
        self.execute(turn, "create_issue", {"title": "Bug", "body": "Fix it"})
        self.execute(turn, "comment", {"number": 4, "body": "Fixed by PR #7"})
        self.execute(turn, "update_issue", {"number": 4, "state": "closed", "state_reason": "completed"})
        self.assertEqual(self.provider.issue["state"], "closed")
        self.assertEqual(len(self.approvals), 3)

    def test_release_draft_publish(self):
        turn = self.turn()
        self.execute(turn, "create_release", {"tag": "v2.4.1", "branch": "main", "name": "v2.4.1", "body": "Changes", "draft": True, "prerelease": False})
        self.execute(turn, "publish_release", {"id": 9})
        self.assertFalse(self.provider.release["draft"])
        self.assertEqual(self.provider.release["target_commitish"], "a" * 40)

    def test_read_only_denies_workflows(self):
        turn = self.turn(False)
        for name in github.WORKFLOW_NAMES:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "not authorized"):
                turn.execute(name, {}, approve=self.approve)

    def test_unknown_issue_write_blocks_retry(self):
        args = {"title": "Bug", "body": "Fix it"}
        self.provider.fail_write = True
        turn = self.turn()
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.execute(turn, "create_issue", args)
        self.provider.fail_write = False
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.execute(turn, "create_issue", args)
        with self.assertRaisesRegex(ValueError, "previous attempt"):
            self.execute(self.turn(), "create_issue", args)
        self.assertEqual(sum(c[1] == "POST" for c in self.provider.calls), 1)

    def test_natural_requests(self):
        for message in ("Create a new issue", "Merge PR #7", "Draft a release", "Publish v2.4.1",
                        "Create a feature branch", "Comment on issue #4", "Reopen issue #4"):
            with self.subTest(message=message):
                self.assertTrue(github.requested(message, True))

    def test_workspace_upload_exact_content_and_scope(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / "project"
        folder.mkdir()
        (folder / "notes.txt").write_bytes(b"new notes\r\n")
        (folder / ".env").write_text("secret")
        turn = self.turn()
        self.execute(turn, "create_branch", {"branch": "dev", "base": "main"})
        args = {"branch": "dev", "message": "Upload", "files": [{"source": "notes.txt", "path": "notes.txt"}]}
        with self.assertRaisesRegex(ValueError, "not authorized"):
            self.execute(turn, "upload_files", args)
        workspace = Workspace(folder)
        turn.workspace_resolver = lambda: workspace
        for source in (".env", "../outside.txt"):
            with self.assertRaises(ValueError):
                self.execute(turn, "upload_files", {**args, "files": [{"source": source, "path": "notes.txt"}]})
        self.assertIn("/commit/", self.execute(turn, "upload_files", args))
        tree = next(c[3] for c in self.provider.calls if c[1] == "POST" and c[2].endswith("/git/trees"))
        self.assertEqual(tree["tree"][0]["content"], "new notes\r\n")

    def test_workspace_changed_during_approval(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / "project"
        folder.mkdir()
        source = folder / "notes.txt"
        source.write_text("before")
        turn = self.turn()
        self.execute(turn, "create_branch", {"branch": "dev", "base": "main"})
        workspace = Workspace(folder)
        turn.workspace_resolver = lambda: workspace
        def approve(*_):
            source.write_text("after")
            return True
        with self.assertRaisesRegex(ValueError, "sources changed"):
            turn.execute("github_upload_files", {"branch": "dev", "message": "Upload", "files": [
                {"source": "notes.txt", "path": "notes.txt"}]}, approve=approve)
        self.assertFalse(any(c[1] == "POST" and c[2].endswith("/git/trees") for c in self.provider.calls))

    def test_binary_file_upload_creates_base64_blob(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / "project"
        folder.mkdir()
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        (folder / "logo.png").write_bytes(png_bytes)
        (folder / "fake.bin").write_bytes(b"\x00-----BEGIN PRIVATE KEY-----\nabc")
        turn = self.turn()
        self.execute(turn, "create_branch", {"branch": "dev", "base": "main"})
        workspace = Workspace(folder)
        turn.workspace_resolver = lambda: workspace
        with self.assertRaisesRegex(ValueError, "Private keys"):
            self.execute(turn, "upload_files", {"branch": "dev", "message": "Bad", "files": [
                {"source": "fake.bin", "path": "fake.bin"}]})
        res = self.execute(turn, "upload_files", {"branch": "dev", "message": "Add logo", "files": [
            {"source": "logo.png", "path": "assets/logo.png"}]})
        self.assertIn("/commit/", res)
        self.assertEqual(len(self.provider.blobs), 1)
        expected_sha = hashlib.sha1(f"blob {len(png_bytes)}\0".encode() + png_bytes).hexdigest()
        self.assertEqual(self.provider.blobs[expected_sha], png_bytes)
        tree = next(c[3] for c in self.provider.calls if c[1] == "POST" and c[2].endswith("/git/trees"))
        self.assertEqual(tree["tree"][0]["sha"], expected_sha)
        self.assertNotIn("content", tree["tree"][0])

    def test_release_asset_upload_and_safety(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / "project"
        folder.mkdir()
        zip_path = folder / "Pipkin-2.4.1.zip"
        zip_bytes = b"PK\x03\x04demo-binary-archive"
        zip_path.write_bytes(zip_bytes)
        turn = self.turn()
        created = self.execute(turn, "create_release", {"tag": "v2.4.1", "branch": "main", "name": "v2.4.1"})
        self.assertIn("release id 9", created)
        self.assertTrue(self.provider.release["draft"], "release defaults to draft when omitted")
        # Not authorized without workspace resolver
        with self.assertRaisesRegex(ValueError, "not authorized"):
            self.execute(turn, "upload_release_asset", {"id": 9, "source": "Pipkin-2.4.1.zip"})
        workspace = Workspace(folder)
        turn.workspace_resolver = lambda: workspace
        res = self.execute(turn, "upload_release_asset", {"id": 9, "source": "Pipkin-2.4.1.zip", "label": "macOS zip"})
        self.assertIn("Pipkin-2.4.1.zip", res)
        self.assertEqual(len(self.provider.asset_uploads), 1)
        self.assertEqual(self.provider.asset_uploads[0][4], zip_bytes)
        # Duplicate asset name on same release is rejected
        turn2 = self.turn()
        turn2.workspace_resolver = lambda: workspace
        with self.assertRaisesRegex(ValueError, "already has an asset"):
            self.execute(turn2, "upload_release_asset", {"id": 9, "source": "Pipkin-2.4.1.zip"})
        # SSRF guard on GitHubAPI.upload_asset rejects non-uploads.github.com hosts
        real_api = github.GitHubAPI("tok")
        with self.assertRaisesRegex(ValueError, "unexpected host"):
            real_api.upload_asset("https://evil.example.com/repos/octo/project/releases/9/assets", "a.zip", "application/zip", b"x")

    def test_optional_defaults_for_small_models(self):
        turn = self.turn()
        self.execute(turn, "create_branch", {"branch": "dev", "base": "main"})
        # Small 3B models sometimes JSON-stringify nested file lists or send stringified bools/numbers
        self.execute(turn, "commit_files", {"branch": "dev", "message": "Edit",
                                            "files": '[{"path": "README.md", "content": "v2\\n"}]'})
        # Omit body and draft on PR, omit body on issue
        pr_res = self.execute(turn, "create_pr", {"head": "dev", "base": "main", "title": "Quick PR", "draft": "false"})
        self.assertIn("/pull/7", pr_res)
        self.assertFalse(self.provider.pr["draft"])
        self.assertIn("checksPassed", self.execute(turn, "review_pr", {"number": "#7"}))
        issue_res = self.execute(turn, "create_issue", {"title": "Short issue"})
        self.assertIn("/issues/4", issue_res)
        rel_res = self.execute(turn, "create_release", {"tag": "v2.4.2", "branch": "main", "name": "v2.4.2", "draft": "true", "prerelease": "false"})
        self.assertIn("release id 9", rel_res)
        self.assertTrue(self.provider.release["draft"])

    def test_local_git_push_branch_and_guards(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo_dir = Path(temp.name) / "project"
        repo_dir.mkdir()
        def git(*args):
            subprocess.run(["git", *args], cwd=repo_dir, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git("init", "-b", "main")
        git("config", "user.name", "Tester")
        git("config", "user.email", "tester@example.com")
        git("remote", "add", "origin", "https://github.com/octo/project.git")
        (repo_dir / "README.md").write_text("base\n")
        git("add", "README.md")
        git("commit", "-m", "Initial commit")
        base_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()
        self.provider.branches["main"] = base_sha

        git("checkout", "-b", "feat-local")
        (repo_dir / "README.md").write_text("feature work\n")
        git("add", "README.md")
        git("commit", "-m", "Add feature work")
        feat_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()

        workspace = Workspace(repo_dir)
        push_calls = []
        def fake_git_runner(cwd, git_args, *, extra_env=None, timeout=30):
            if git_args and git_args[0] == "push":
                push_calls.append((git_args, extra_env))
                # Token must never appear in argv
                self.assertFalse(any(self.provider.token in arg for arg in git_args))
                self.assertIn("GIT_CONFIG_VALUE_0", extra_env)
                self.provider.branches["feat-local"] = feat_sha
                return 0, "ok\n", ""
            return github_workflows._default_git_runner(cwd, git_args, extra_env=extra_env, timeout=timeout)

        # Pushing to default branch is refused
        turn = self.turn()
        turn.workspace_resolver = lambda: workspace
        turn.git_runner = fake_git_runner
        with self.assertRaisesRegex(ValueError, "default branch"):
            self.execute(turn, "push_branch", {"local_branch": "feat-local", "remote_branch": "main"})

        # Pushing feature branch succeeds with approval
        turn = self.turn()
        turn.workspace_resolver = lambda: workspace
        turn.git_runner = fake_git_runner
        res = self.execute(turn, "push_branch", {})
        self.assertIn("/tree/feat-local", res)
        self.assertEqual(len(push_calls), 1)
        self.assertEqual(self.provider.branches["feat-local"], feat_sha)

        # Workflow file changes in local commits are refused
        (repo_dir / ".github" / "workflows").mkdir(parents=True)
        (repo_dir / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
        git("add", ".github/workflows/ci.yml")
        git("commit", "-m", "Touch workflow")
        turn = self.turn()
        turn.workspace_resolver = lambda: workspace
        turn.git_runner = fake_git_runner
        with self.assertRaisesRegex(ValueError, "workflows"):
            self.execute(turn, "push_branch", {"local_branch": "feat-local", "remote_branch": "feat-wf"})

        # Mismatched origin remote is refused
        git("remote", "set-url", "origin", "https://github.com/other/project.git")
        turn = self.turn()
        turn.workspace_resolver = lambda: workspace
        turn.git_runner = fake_git_runner
        with self.assertRaisesRegex(ValueError, "origin"):
            self.execute(turn, "push_branch", {"local_branch": "feat-local", "remote_branch": "feat-other"})




class HardenedGitRunnerTest(unittest.TestCase):
    """A folder's own .git/config must not make Pipkin run programs while it inspects or pushes commits."""

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_fsmonitor_and_external_diff_from_repo_config_never_run(self):
        import github_workflows
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run = lambda *args: subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            run("init", "-q")
            run("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "one")
            (root / "a.txt").write_text("hi\n")
            run("add", "a.txt")
            marker = root / "ran"
            run("config", "core.fsmonitor", f"touch '{marker}-fsmonitor'; echo")
            run("config", "diff.external", f"sh -c 'touch \"{marker}-diff\"'")
            for args in (["status", "--porcelain"], ["diff", "--cached"], ["log", "-p", "-n", "1"]):
                github_workflows._default_git_runner(root, args)
            self.assertEqual(sorted(p.name for p in root.iterdir() if p.name.startswith("ran")), [])
