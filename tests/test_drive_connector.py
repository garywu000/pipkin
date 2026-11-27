import copy
import json
import tempfile
import unittest
import base64
import hashlib
import urllib.parse
import urllib.request
import urllib.error
from unittest import mock

import agent
import server
from pathlib import Path

import drive_connector as drive


class FakeAPI:
    def __init__(self):
        self.posts = []
        self.fail_write = False
        self.items = {
            "root": {"id": "root", "name": "Test folder", "mimeType": drive.FOLDER,
                     "capabilities": {"canAddChildren": True}, "version": "1"},
            "doc": {"id": "doc", "name": "Document", "mimeType": drive.DOC,
                    "parents": ["root"], "capabilities": {"canEdit": True}, "version": "1"},
            "outside": {"id": "outside", "name": "Outside", "mimeType": drive.DOC,
                        "parents": ["other"]},
            "other": {"id": "other", "name": "Other", "mimeType": drive.FOLDER},
            "shortcut": {"id": "shortcut", "name": "Shortcut",
                         "mimeType": "application/vnd.google-apps.shortcut", "parents": ["root"]},
        }
        self.document = {"revisionId": "r1", "tabs": [{
            "tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": [
                {"paragraph": {"elements": [{"startIndex": 1, "textRun": {"content": "😀 Hello world\n"}}]}}]}}}]}
        self.last_query = None

    def call(self, path, *, docs=False, query=None, method="GET", data=None, raw=False):
        if method == "POST":
            self.posts.append((path, copy.deepcopy(data)))
            if self.fail_write:
                raise ValueError("Network failure")
            if docs:
                return {"documentId": "doc"}
            return {"id": "created", "parents": data["parents"], "mimeType": data["mimeType"], "name": data["name"]}
        if path == "about":
            return {"user": {"permissionId": "account", "emailAddress": "test@example.com"}}
        if path == "files":
            self.last_query = query
            return {"files": [copy.deepcopy(self.items["doc"])]}
        if path.startswith("files/"):
            return copy.deepcopy(self.items[path.split("/")[1]])
        if path == "documents/doc":
            return copy.deepcopy(self.document)
        raise AssertionError(path)


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.connection = drive.Connection(api_factory=lambda _: self.api)
        self.connection._oauth = {"scope": drive.WRITE_SCOPE}
        self.connection.select("root")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / "writes.db"

    def turn(self, writes=True):
        return self.connection.turn(writes=writes, journal=self.journal)

    def test_natural_routing(self):
        for prompt in ["Find files in my Google Drive", "Create a Google Doc", "revise the document in Google Docs", "@drive list"]:
            self.assertTrue(drive.requested(prompt), prompt)
        for prompt in ["Explain Google Drive", "How do I create a Google Doc?", "Find files in my local drive", "Read the README in this repository"]:
            self.assertFalse(drive.requested(prompt), prompt)

    def test_scoped_list_and_read(self):
        turn = self.turn()
        result = turn.execute("drive_list", {"query": "a'b\\c"})
        self.assertIn("Document", result)
        self.assertIn("'root' in parents", self.api.last_query["q"])
        self.assertIn("a\\'b\\\\c", self.api.last_query["q"])
        self.assertIn("Hello world", turn.execute("drive_read", {"id": "doc"}))
        for ident in ["outside", "shortcut", "../doc"]:
            with self.assertRaises(ValueError):
                turn.execute("drive_read", {"id": ident})

    def test_explicit_drive_destination(self):
        turn = self.turn()
        turn.validate_destination("Read https://docs.google.com/document/d/doc/edit")
        with self.assertRaises(ValueError):
            turn.validate_destination("Edit https://docs.google.com/document/d/outside/edit")
        with self.assertRaises(ValueError):
            turn.validate_destination("Create a doc in https://drive.google.com/drive/folders/other")

    def test_rejects_text_google_would_strip(self):
        for value in ("a\x01b", "a\ud800b", "a\ue000b"):
            with self.assertRaises(ValueError):
                drive.bounded_text(value)

    def test_read_only_and_denied_write(self):
        self.assertEqual({t["function"]["name"] for t in self.turn(False).tools}, drive.READ_NAMES)
        with self.assertRaises(ValueError):
            self.turn(False).execute("drive_create_doc", {"name": "Draft"}, approve=lambda *_: True)
        result = self.turn().execute("drive_create_doc", {"name": "Draft"}, approve=lambda *_: False)
        self.assertIn("declined", result)
        self.assertEqual(self.api.posts, [])

    def test_approval_receipt_and_duplicate(self):
        details = []
        args = {"name": "Draft"}
        result = self.turn().execute("drive_create_doc", args,
                                     approve=lambda kind, title, detail: details.append(json.loads(detail)) or True)
        self.assertIn("created", result)
        self.assertEqual(details[0]["payload"]["parents"], ["root"])
        self.assertEqual(len(self.api.posts), 1)

    def test_repeat_in_same_request_is_skipped_but_later_request_may_repeat(self):
        turn = self.turn()
        turn.execute("drive_create_folder", {"name": "Notes"}, approve=lambda *_: True)
        asked = []
        result = turn.execute("drive_create_folder", {"name": "Notes"}, approve=lambda *a: asked.append(a) or True)
        self.assertIn("already succeeded", result)
        self.assertEqual((asked, len(self.api.posts)), ([], 1))
        self.assertIn("succeeded", self.turn().execute("drive_create_folder", {"name": "Notes"}, approve=lambda *_: True))
        self.assertEqual(len(self.api.posts), 2)

    def test_refused_write_does_not_lock_the_folder(self):
        def refuse(*_args, **_kwargs):
            raise drive.DriveHTTPError(403)
        original = self.api.call
        self.api.call = lambda path, **kw: refuse() if kw.get("method") == "POST" else original(path, **kw)
        with self.assertRaisesRegex(ValueError, "Nothing was written"):
            self.turn().execute("drive_create_doc", {"name": "Draft"}, approve=lambda *_: True)
        self.api.call = original
        self.assertIn("succeeded", self.turn().execute("drive_create_doc", {"name": "Draft"}, approve=lambda *_: True))

    def test_server_error_stays_uncertain(self):
        original = self.api.call
        def fail(path, **kw):
            if kw.get("method") == "POST":
                raise drive.DriveHTTPError(503)
            return original(path, **kw)
        self.api.call = fail
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.turn().execute("drive_create_doc", {"name": "Draft"}, approve=lambda *_: True)
        self.api.call = original
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.turn().execute("drive_create_doc", {"name": "Other"}, approve=lambda *_: True)

    def test_changed_folder_and_disconnect(self):
        def move(*_):
            self.api.items["doc"]["parents"] = ["other"]
            return True
        with self.assertRaises(ValueError):
            self.turn().execute("drive_append_doc", {"id": "doc", "text": "test"}, approve=move)
        turn = self.turn()
        self.connection.disconnect()
        with self.assertRaises(ValueError):
            turn.execute("drive_list", {})
        self.assertEqual(self.api.posts, [])

    def test_revision_and_utf16(self):
        result = self.turn().execute("drive_replace_doc", {"id": "doc", "old": "Hello", "new": "Hi"},
                                     approve=lambda *_: True)
        self.assertIn("succeeded", result)
        payload = self.api.posts[0][1]
        self.assertEqual(payload["writeControl"], {"requiredRevisionId": "r1"})
        self.assertEqual(payload["requests"][0]["deleteContentRange"]["range"],
                         {"startIndex": 4, "endIndex": 9, "tabId": "t.0"})

    def test_ambiguous_write_blocks_later_turn(self):
        self.api.fail_write = True
        with self.assertRaises(ValueError):
            self.turn().execute("drive_create_doc", {"name": "Draft"}, approve=lambda *_: True)
        self.api.fail_write = False
        with self.assertRaises(ValueError):
            self.turn().execute("drive_create_folder", {"name": "Other"}, approve=lambda *_: True)
        self.assertEqual(len(self.api.posts), 1)

    def test_recursive_search_and_links(self):
        self.api.items["child"] = {"id": "child", "name": "Folder", "mimeType": drive.FOLDER,
                                   "parents": ["root"]}
        self.api.items["doc"]["parents"] = ["child"]
        original = self.api.call
        def call(path, **kwargs):
            if path == "files":
                parent = kwargs["query"]["q"].split("'")[1]
                return {"files": [copy.deepcopy(item) for item in self.api.items.values()
                                  if item.get("parents") == [parent]]}
            return original(path, **kwargs)
        self.api.call = call
        result = self.turn().execute("drive_search", {"query": "document"})
        self.assertIn("Document", result)
        self.assertEqual(drive.result_links(result), [drive.file_url("doc")])
        self.assertNotIn("Outside", result)

    def test_stale_revision_refuses_before_post(self):
        def edit(*_):
            self.api.document["revisionId"] = "r2"
            return True
        with self.assertRaisesRegex(ValueError, "Document changed"):
            self.turn().execute("drive_append_doc", {"id": "doc", "text": "Hi"}, approve=edit)
        self.assertEqual(self.api.posts, [])
        self.assertFalse(self.journal.exists())

    def test_agent_auto_mode_is_isolated_and_asks(self):
        turn = self.turn()
        replies = iter([{"tool_calls": [{"function": {"name": "drive_create_doc", "arguments": {"name": "Draft"}}}]},
                        {"content": "Declined."}])
        approval = mock.Mock(return_value=False)
        def model(messages, tools, think):
            self.assertTrue(all(t["function"]["name"].startswith("drive_") for t in tools))
            return next(replies)
        worker = agent.Agent(model, lambda *_a, **_kw: None, lambda: None, approval,
                             mode="auto", github=turn, web=True, memory=True)
        worker.run(turn.guidance, [], "Create a Google Doc")
        approval.assert_called_once()
        self.assertEqual(approval.call_args.args[0], "drive")
        self.assertEqual(self.api.posts, [])

    def test_combined_router_prefers_explicit_provider(self):
        with mock.patch.object(server.GITHUB, "route", return_value={"github": True, "generation": 7}), mock.patch.object(server.DRIVE, "route", return_value={"drive": True, "generation": 9}):
            self.assertEqual(server.connector_routes("Upload a file to Google Drive"),
                             {"github": False, "generation": 7, "drive": True, "driveGeneration": 9})
            self.assertTrue(server.connector_routes("Copy my GitHub repository into Google Drive")["github"])

    def test_combined_router_actual_cross_provider_request(self):
        routes = server.connector_routes("Copy my GitHub repository into Google Drive")
        self.assertTrue(routes["github"])
        self.assertTrue(routes["drive"])

    def test_oauth_pkce_and_state_callback(self):
        token_request = mock.Mock(return_value={"access_token": "token", "refresh_token": "refresh",
                                                 "expires_in": 3600, "scope": drive.READ_SCOPE})
        connection = drive.Connection(token_request=token_request)
        self.addCleanup(connection.disconnect)
        client = {"installed": {"client_id": "fake.apps.googleusercontent.com", "client_secret": "fake-secret"}}
        with mock.patch.object(drive.webbrowser, "open", return_value=True) as browser:
            status = connection.start(client)
        self.assertTrue(status["pending"])
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(browser.call_args.args[0]).query)
        callback = params["redirect_uri"][0]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            opener.open(callback + "?state=wrong&code=bad", timeout=5)
        self.assertEqual(rejected.exception.code, 400)
        token_request.assert_not_called()
        with opener.open(callback + "?" + urllib.parse.urlencode({"state": params["state"][0], "code": "good"}), timeout=5) as response:
            self.assertEqual(response.status, 200)
        sent = token_request.call_args.kwargs["data"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(challenge, params["code_challenge"][0])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertTrue(connection.state()["signedIn"])
        self.assertNotIn("token", json.dumps(connection.state()))
        connection.disconnect()
        self.assertFalse(connection.state()["signedIn"])


    def test_oauth_scope_validation(self):
        response = {"access_token": "token", "refresh_token": "refresh", "expires_in": 3600,
                    "scope": drive.READ_SCOPE}
        token = self.connection._token(response, drive.READ_SCOPE, "client", "secret")
        self.assertEqual(token["scope"], drive.READ_SCOPE)
        with self.assertRaises(ValueError):
            self.connection._token({**response, "scope": drive.WRITE_SCOPE}, drive.READ_SCOPE, "client", "secret")
        with self.assertRaises(ValueError):
            self.connection._token({**response, "scope": drive.READ_SCOPE + " unrelated"}, drive.READ_SCOPE, "client", "secret")


if __name__ == "__main__":
    unittest.main()
