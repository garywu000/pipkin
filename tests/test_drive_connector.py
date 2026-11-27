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

    def test_credential_persistence_and_disconnect_wipe(self):
        store = drive.MemoryStore()
        store.persistent = True
        api = FakeAPI()
        c1 = drive.Connection(api_factory=lambda _: api, store=store)
        c1._oauth = {
            "access": "acc1", "refresh": "ref1", "expires": 9e9,
            "client_id": "fake.apps.googleusercontent.com", "secret": "fake-secret",
            "scope": drive.WRITE_SCOPE,
        }
        c1._saved_client = {"client_id": "fake.apps.googleusercontent.com", "secret": "fake-secret"}
        c1.select("root")
        self.assertTrue(c1.state()["persisted"])
        self.assertTrue(c1.state()["hasSavedClient"])

        # Simulate restarting Pipkin with a brand-new Connection backed by the same store.
        c2 = drive.Connection(api_factory=lambda _: api, store=store)
        st = c2.state()
        self.assertTrue(st["signedIn"])
        self.assertTrue(st["hasSavedClient"])
        self.assertEqual(st["connection"]["folder"], "root")
        self.assertNotIn("token", json.dumps(st))

        # Disconnect wipes persisted credentials cleanly.
        c2.disconnect()
        c3 = drive.Connection(api_factory=lambda _: api, store=store)
        self.assertFalse(c3.state()["signedIn"])
        self.assertFalse(c3.state()["hasSavedClient"])
        self.assertIsNone(c3.state()["connection"])

    def test_browse_and_file_specific_scope(self):
        self.api.items["sub"] = {"id": "sub", "name": "Reports", "mimeType": drive.FOLDER, "parents": ["root"]}
        self.api.items["doc2"] = {"id": "doc2", "name": "Secret", "mimeType": drive.DOC, "parents": ["root"], "capabilities": {"canEdit": True}, "version": "1"}
        original = self.api.call

        def call(path, **kw):
            if path == "files":
                return {"files": [copy.deepcopy(self.api.items["sub"]), copy.deepcopy(self.api.items["doc"]),
                                  copy.deepcopy(self.api.items["doc2"]), copy.deepcopy(self.api.items["shortcut"])]}
            return original(path, **kw)

        self.api.call = call
        browsed = self.connection.browse("root")
        self.assertEqual([f["id"] for f in browsed["folders"]], ["sub"])
        self.assertEqual({f["id"] for f in browsed["files"]}, {"doc", "doc2"})

        # Narrow grant to ONLY "doc" inside "root".
        st = self.connection.select("root", files=["doc"])
        self.assertEqual(len(st["connection"]["allowedFiles"]), 1)
        turn = self.turn()
        listed = json.loads(turn.execute("drive_list", {}).split("\n", 1)[1])
        self.assertEqual({f["id"] for f in listed["files"]}, {"sub", "doc"})
        self.assertIn("Hello world", turn.execute("drive_read", {"id": "doc"}))
        with self.assertRaisesRegex(ValueError, "file-specific"):
            turn.execute("drive_read", {"id": "doc2"})
        with self.assertRaises(ValueError):
            self.connection.select("root", files=["outside"])

    def test_docs_tables_multitab_cross_run_and_section_insert(self):
        self.api.document = {
            "revisionId": "r10",
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Overview"},
                    "documentTab": {"body": {"content": [
                        {"startIndex": 1, "endIndex": 10, "paragraph": {
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                            "elements": [{"startIndex": 1, "textRun": {"content": "Overview\n"}}],
                        }},
                        {"startIndex": 10, "endIndex": 32, "paragraph": {
                            "elements": [
                                {"startIndex": 10, "textRun": {"content": "Status: "}},
                                {"startIndex": 18, "textRun": {"content": "In Progress\n"}},
                                {"startIndex": 30, "inlineObjectElement": {"inlineObjectId": "img1"}},
                            ],
                        }},
                        {"startIndex": 33, "endIndex": 80, "table": {"tableRows": [
                            {"tableCells": [
                                {"content": [{"paragraph": {"elements": [{"startIndex": 35, "textRun": {"content": "Metric\n"}}]}}]},
                                {"content": [{"paragraph": {"elements": [{"startIndex": 45, "textRun": {"content": "Value\n"}}]}}]},
                            ]},
                            {"tableCells": [
                                {"content": [{"paragraph": {"elements": [{"startIndex": 55, "textRun": {"content": "Users\n"}}]}}]},
                                {"content": [{"paragraph": {"elements": [{"startIndex": 65, "textRun": {"content": "4200\n"}}]}}]},
                            ]},
                        ]}},
                    ]}},
                },
                {
                    "tabProperties": {"tabId": "t.1", "title": "Appendix"},
                    "documentTab": {"body": {"content": [
                        {"startIndex": 1, "endIndex": 15, "paragraph": {
                            "elements": [{"startIndex": 1, "textRun": {"content": "Appendix note\n"}}],
                        }},
                    ]}},
                },
            ],
        }
        turn = self.turn()
        read_main = json.loads(turn.execute("drive_read", {"id": "doc"}).split("\n", 1)[1])
        self.assertIn("# Overview", read_main["text"])
        self.assertIn("| Metric | Value |", read_main["text"])
        self.assertIn("| Users | 4200 |", read_main["text"])
        self.assertEqual(len(read_main["tabs"]), 2)

        read_tab2 = json.loads(turn.execute("drive_read", {"id": "doc", "tab_id": "t.1"}).split("\n", 1)[1])
        self.assertIn("Appendix note", read_tab2["text"])

        # Cross-run replacement spanning "Status: " + "In Progress" inside a Doc with an image + table.
        res = turn.execute("drive_replace_doc", {"id": "doc", "old": "Status: In Progress", "new": "Status: Done"},
                           approve=lambda *_: True)
        self.assertIn("succeeded", res)
        req0 = self.api.posts[-1][1]["requests"][0]["deleteContentRange"]["range"]
        self.assertEqual(req0, {"startIndex": 10, "endIndex": 29, "tabId": "t.0"})

        # Table cell replacement.
        res2 = turn.execute("drive_replace_doc", {"id": "doc", "old": "4200", "new": "5100"}, approve=lambda *_: True)
        self.assertIn("succeeded", res2)
        self.assertEqual(self.api.posts[-1][1]["requests"][0]["deleteContentRange"]["range"],
                         {"startIndex": 65, "endIndex": 69, "tabId": "t.0"})

        # Section insert after "Overview".
        res3 = turn.execute("drive_insert_section", {
            "id": "doc", "heading": "Next Steps", "level": "2", "body": "Ship beta.", "after": "Overview",
        }, approve=lambda *_: True)
        self.assertIn("succeeded", res3)
        reqs = self.api.posts[-1][1]["requests"]
        self.assertEqual(reqs[0]["insertText"]["location"], {"index": 10, "tabId": "t.0"})
        self.assertEqual(reqs[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"], "HEADING_2")

    def test_create_doc_with_initial_content(self):
        original = self.api.call

        def call(path, **kw):
            if path == "documents/created":
                return {"revisionId": "r0", "tabs": [{"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": []}}}]}
            if path == "documents/created:batchUpdate":
                self.api.posts.append((path, copy.deepcopy(kw["data"])))
                return {"documentId": "created"}
            return original(path, **kw)

        self.api.call = call
        res = self.turn().execute("drive_create_doc", {"name": "Launch Plan", "content": "# Launch Plan\nReady to go."},
                                  approve=lambda *_: True)
        data = json.loads(res.split("\n", 1)[1])
        self.assertTrue(data["populated"])
        self.assertEqual(self.api.posts[1][0], "documents/created:batchUpdate")
        self.assertEqual(self.api.posts[1][1]["requests"][1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"], "HEADING_1")

    def test_sheets_read_and_write(self):
        self.api.items["sheet1"] = {"id": "sheet1", "name": "Budget", "mimeType": drive.SHEET,
                                    "parents": ["root"], "capabilities": {"canEdit": True}, "version": "3"}
        sheet_calls = []
        original = self.api.call

        def call(path, **kw):
            if kw.get("sheets"):
                sheet_calls.append((kw.get("method", "GET"), path, copy.deepcopy(kw.get("data"))))
                if path == "spreadsheets/sheet1":
                    return {"properties": {"title": "Budget"}, "sheets": [{"properties": {"sheetId": 0, "title": "Q4", "gridProperties": {"rowCount": 50, "columnCount": 10}}}]}
                if path.startswith("spreadsheets/sheet1/values/") and kw.get("method", "GET") == "GET":
                    return {"range": "Q4!A1:B2", "values": [["Item", "Cost"], ["Hosting", "20"]]}
                if kw.get("method") == "PUT":
                    return {"spreadsheetId": "sheet1", "updatedRange": "Q4!A2:B2"}
                if kw.get("method") == "POST":
                    return {"spreadsheetId": "sheet1", "updates": {"updatedRange": "Q4!A3:B3"}}
            return original(path, **kw)

        self.api.call = call
        turn = self.turn()
        read_res = json.loads(turn.execute("drive_read", {"id": "sheet1"}).split("\n", 1)[1])
        self.assertEqual(read_res["sheets"][0]["title"], "Q4")
        self.assertEqual(read_res["values"][1], ["Hosting", "20"])

        # Test small-model stringified JSON 2D array coercion + approval detail preview.
        details = []
        upd = turn.execute("drive_update_sheet", {
            "id": "sheet1", "range": "Q4!A2:B2", "values": '[["Hosting", 25]]', "mode": "update",
        }, approve=lambda k, t, d: details.append(json.loads(d)) or True)
        self.assertIn("succeeded", upd)
        self.assertEqual(details[0]["existingValues"][1], ["Hosting", "20"])
        self.assertEqual(details[0]["values"], [["Hosting", 25]])

        app = turn.execute("drive_update_sheet", {
            "id": "sheet1", "range": "Q4!A:B", "values": "[['Domain', 12]]", "mode": "append",
        }, approve=lambda *_: True)
        self.assertIn("Q4!A3:B3", app)
        # Small models sometimes pass the selected folder's display name instead of its ID.
        self.assertIn("Document", turn.execute("drive_list", {"folder": "Test folder"}))

    def test_slides_read_and_write(self):
        self.api.items["deck1"] = {"id": "deck1", "name": "Demo Deck", "mimeType": drive.SLIDES,
                                   "parents": ["root"], "capabilities": {"canEdit": True}, "version": "2"}
        pres_data = {
            "presentationId": "deck1", "title": "Demo Deck", "revisionId": "s1",
            "slides": [{
                "objectId": "p1",
                "pageElements": [{"shape": {"text": {"textElements": [{"textRun": {"content": "Q3 Roadmap\n"}}]}}}],
                "slideProperties": {"notesPage": {"pageElements": [{"shape": {"text": {"textElements": [{"textRun": {"content": "Keep brief\n"}}]}}}]}},
            }],
        }
        original = self.api.call

        def call(path, **kw):
            if kw.get("slides"):
                if kw.get("method") == "POST":
                    self.api.posts.append((path, copy.deepcopy(kw["data"])))
                    return {"presentationId": "deck1"}
                return copy.deepcopy(pres_data)
            return original(path, **kw)

        self.api.call = call
        turn = self.turn()
        read_res = json.loads(turn.execute("drive_read", {"id": "deck1"}).split("\n", 1)[1])
        self.assertEqual(read_res["slides"][0]["text"], "Q3 Roadmap")
        self.assertEqual(read_res["slides"][0]["notes"], "Keep brief")

        rep = turn.execute("drive_update_slides", {
            "id": "deck1", "action": "replace_text", "old": "Q3", "new": "Q4", "slide_index": "1",
        }, approve=lambda *_: True)
        self.assertIn("succeeded", rep)
        self.assertEqual(self.api.posts[-1][1]["requests"][0]["replaceAllText"]["pageObjectIds"], ["p1"])

        add = turn.execute("drive_update_slides", {
            "id": "deck1", "action": "add_slide", "title": "Summary", "body": "All green.",
        }, approve=lambda *_: True)
        self.assertIn("succeeded", add)
        self.assertEqual(self.api.posts[-1][1]["requests"][0]["createSlide"]["slideLayoutReference"]["predefinedLayout"], "TITLE_AND_BODY")

    def test_binary_metadata_and_workspace_upload(self):
        self.api.items["img1"] = {"id": "img1", "name": "logo.png", "mimeType": "image/png", "size": "2048",
                                  "parents": ["root"], "version": "1"}
        turn = self.turn()
        # Upload tool is hidden when no workspace_resolver is attached.
        self.assertNotIn("drive_upload_file", {t["function"]["name"] for t in turn.tools})
        meta = json.loads(turn.execute("drive_read", {"id": "img1"}).split("\n", 1)[1])
        self.assertTrue(meta["binary"])
        self.assertEqual(meta["mimeType"], "image/png")

        # Attach workspace and test binary upload + PEM key refusal.
        ws_dir = Path(self.tmp.name) / "ws"
        ws_dir.mkdir()
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        (ws_dir / "chart.png").write_bytes(png_bytes)
        (ws_dir / "secret.bin").write_bytes(b"\x00\x01-----BEGIN PRIVATE KEY-----\nABC\n")
        ws = agent.Workspace(ws_dir)
        turn.workspace_resolver = lambda: ws
        self.assertIn("drive_upload_file", {t["function"]["name"] for t in turn.tools})

        uploaded = []
        original = self.api.call

        def call(path, **kw):
            if kw.get("upload"):
                uploaded.append((path, kw.get("content_type"), kw.get("data")))
                return {"id": "up1", "name": "chart.png", "mimeType": "image/png", "parents": ["root"], "size": str(len(png_bytes))}
            return original(path, **kw)

        self.api.call = call
        with self.assertRaisesRegex(ValueError, "Private keys"):
            turn.execute("drive_upload_file", {"source_path": "secret.bin"}, approve=lambda *_: True)

        details = []
        res = turn.execute("drive_upload_file", {"source_path": "chart.png"},
                           approve=lambda k, t, d: details.append(json.loads(d)) or True)
        self.assertIn("up1", res)
        self.assertEqual(details[0]["bytes"], len(png_bytes))
        self.assertEqual(len(uploaded), 1)
        self.assertIn("multipart/related", uploaded[0][1])
        self.assertIn(png_bytes, uploaded[0][2])

    def test_routing_covers_sheets_slides_and_uploads(self):
        for prompt in [
            "Read my Google Sheet Budget",
            "Update cells A2:B3 in my Google Sheets tracker",
            "Add a slide to my Google Slides deck",
            "Upload chart.png from my local workspace to Google Drive",
        ]:
            self.assertTrue(drive.requested(prompt), prompt)
        self.assertTrue(drive.local_upload_requested("Upload chart.png from my workspace to Google Drive"))
        self.assertFalse(drive.local_upload_requested("List files in Google Drive"))


if __name__ == "__main__":
    unittest.main()


class PickerAPI(FakeAPI):
    """Like Drive with drive.file: items Pipkin wasn't given access to come back 404."""

    def __init__(self, hidden=()):
        super().__init__()
        self.hidden = set(hidden)

    def call(self, path, **kwargs):
        if path.startswith("files/") and (path.split("/")[1] in self.hidden or path.split("/")[1] not in self.items):
            raise drive.DriveHTTPError(404)
        return super().call(path, **kwargs)


class PickerSignInTests(unittest.TestCase):
    """Pipkin's own Google sign-in: only what the user picks in Google's picker (drive.file)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / "writes.db"

    def connect(self, picked, api=None, writes=True):
        self.api = api or PickerAPI(hidden={"other"})
        connection = drive.Connection(api_factory=lambda _: self.api)
        connection._oauth = {"scope": drive.FILE_SCOPE}
        with connection._lock:
            connection._grant_from_pick(picked, writes)
        return connection

    def test_picked_ids_parsing(self):
        self.assertEqual(drive.picked_ids({"picked_file_ids": ["a1,b_2-c"]}), ["a1", "b_2-c"])
        self.assertEqual(drive.picked_ids({}), [])
        with self.assertRaises(ValueError):
            drive.picked_ids({"picked_file_ids": ["ok,../etc"]})
        with self.assertRaises(ValueError):
            drive.picked_ids({"picked_file_ids": [",".join(f"f{i}" for i in range(drive.MAX_ALLOWED_FILES + 1))]})

    def test_picked_folder_covers_its_contents_only(self):
        turn = self.connect(["root"]).turn(writes=True, journal=self.journal)
        self.assertEqual(turn._inside("doc")["id"], "doc")
        with self.assertRaises(ValueError):
            turn._inside("outside")  # its parent folder wasn't picked and isn't visible
        self.assertIn("Test folder", turn.guidance)
        listing = json.loads(turn.execute("drive_list", {}).split("\n", 1)[1])
        self.assertEqual([f["id"] for f in listing["files"]], ["root"])

    def test_picked_file_without_a_folder(self):
        turn = self.connect(["doc"]).turn(writes=True, journal=self.journal)
        self.assertEqual(turn._inside("doc")["id"], "doc")
        with self.assertRaisesRegex(ValueError, "No folder was picked"):
            turn.execute("drive_create_doc", {"name": "New"}, approve=lambda *_: True)
        self.assertEqual(self.api.posts, [])

    def test_whole_drive_tools_are_refused(self):
        connection = self.connect(["root"])
        with self.assertRaisesRegex(ValueError, "picker"):
            connection.browse("root")
        with self.assertRaisesRegex(ValueError, "picker"):
            connection.select("root")

    def test_adding_more_keeps_earlier_picks_and_remove_works(self):
        connection = self.connect(["doc"])
        with connection._lock:
            connection._grant_from_pick(["root"], True, connection._grant)
        self.assertEqual([i["id"] for i in connection.status()["items"]], ["doc", "root"])
        connection.forget_item("doc")
        self.assertEqual([i["id"] for i in connection.status()["items"]], ["root"])
        self.assertEqual(connection.status()["folder"], "root")

    def test_nothing_picked_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Pick at least one"):
            self.connect([])

    def test_token_must_be_drive_file_only(self):
        good = {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": drive.FILE_SCOPE}
        self.assertEqual(drive.Connection._token(good, drive.FILE_SCOPE, "id", "s")["scope"], drive.FILE_SCOPE)
        for scopes in (drive.FILE_SCOPE + " " + drive.WRITE_SCOPE, drive.READ_SCOPE):
            with self.assertRaises(ValueError):
                drive.Connection._token({**good, "scope": scopes}, drive.FILE_SCOPE, "id", "s")

    def test_saved_sign_in_restores_picks(self):
        store = drive.MemoryStore()
        connection = self.connect(["root"])
        connection._oauth = {"access": "", "refresh": "r", "expires": 0.0, "client_id": "c", "secret": "s", "scope": drive.FILE_SCOPE}
        connection._store = store
        connection._persist()
        restored = drive.Connection(api_factory=lambda _: self.api, store=store)
        self.assertEqual(restored.status()["mode"], "picked")
        self.assertEqual([i["id"] for i in restored.status()["items"]], ["root"])
        self.assertEqual(restored.state()["mode"], "picked")

    def test_writes_toggle_is_respected(self):
        connection = self.connect(["root"], writes=False)
        self.assertNotIn("drive_create_doc", {t["function"]["name"] for t in connection.turn(writes=True, journal=self.journal).tools})
        connection.set_writes(True)
        self.assertIn("drive_create_doc", {t["function"]["name"] for t in connection.turn(writes=True, journal=self.journal).tools})

    def test_picker_needs_pipkins_client(self):
        with mock.patch.object(drive, "builtin_client", return_value=None):
            with self.assertRaisesRegex(ValueError, "doesn't include Google sign-in"):
                drive.Connection().start(picker=True)

    def test_builtin_client_file(self):
        path = Path(self.tmp.name) / "google-client.json"
        path.write_text(json.dumps({"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-x_y"}}))
        with mock.patch.dict(drive.os.environ, {"PIPKIN_GOOGLE_CLIENT": str(path)}):
            self.assertEqual(drive.builtin_client(), ("123-abc.apps.googleusercontent.com", "GOCSPX-x_y"))
        path.write_text("{}")
        with mock.patch.dict(drive.os.environ, {"PIPKIN_GOOGLE_CLIENT": str(path)}):
            self.assertIsNone(drive.builtin_client())

    def test_picker_url_and_callback(self):
        opened = []
        responses = {"about": {"user": {"permissionId": "account", "emailAddress": "test@example.com"}}}
        api = PickerAPI(hidden={"other"})
        connection = drive.Connection(api_factory=lambda _: api, token_request=lambda *a, **k: {
            "access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": drive.FILE_SCOPE})
        with mock.patch.object(drive, "builtin_client", return_value=("123-abc.apps.googleusercontent.com", "s")), \
                mock.patch.object(drive.webbrowser, "open", side_effect=lambda url: opened.append(url) or True):
            connection.start(picker=True, writes=True)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query)
        self.assertEqual(query["scope"], [drive.FILE_SCOPE])
        self.assertEqual((query["trigger_onepick"], query["allow_folder_selection"], query["prompt"]), (["true"], ["true"], ["consent"]))
        self.assertIn("code_challenge", query)
        callback = urllib.parse.urlencode({"state": query["state"][0], "code": "c", "picked_file_ids": "root,doc", "scope": drive.FILE_SCOPE})
        with urllib.request.urlopen(query["redirect_uri"][0] + "?" + callback, timeout=5) as response:
            self.assertEqual(response.status, 200)
        state = connection.state()
        self.assertEqual((state["mode"], state["pending"], state["error"]), ("picked", False, None))
        self.assertEqual([i["id"] for i in state["connection"]["items"]], ["root", "doc"])
        self.assertTrue(state["connection"]["writes"])
