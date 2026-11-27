import hashlib
import io
import tarfile
import tempfile
import subprocess
import shutil
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import natural_voice as nv


def make_pack(extra=None, skip=()):
    """A small stand-in for the Kokoro tarball."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:bz2") as tar:
        files = {name: f"{name} contents".encode() for name in nv.KEEP if name not in skip}
        files["lexicon-us-en.txt"] = b"hello h e l o\nworld w o r l d\npipkin p i p k i n\n"
        files["espeak-ng-data/en_dict"] = b"not needed"
        files.update(extra or {})
        for name, content in files.items():
            info = tarfile.TarInfo(f"{nv.PACK['name']}/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return data.getvalue()


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeHelper:
    """Speaks the pipkin-tts protocol: ready line, then one ok line per request, writing a small WAV."""

    def __init__(self, command, **kwargs):
        self.command = command
        self.requests = []
        self.returncode = None
        self.stdin = self
        self._replies = ["ready 24000 54\n"]
        self.stdout = self

    def write(self, line):
        self.requests.append(line)
        sid, speed, path, text = line.rstrip("\n").split("\t", 3)
        Path(path).write_bytes(b"RIFF" + text.encode())
        self._replies.append("ok 100 24000\n")

    def flush(self):
        pass

    def readline(self):
        return self._replies.pop(0) if self._replies else ""

    def poll(self):
        return self.returncode

    def close(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


class PrepareTest(unittest.TestCase):
    def test_numbers_times_money_and_acronyms(self):
        said = nv.prepare("Quiz at 3:30 pm, worth 25% and $12.50; 1,500 users read the PDF on June 12th, 2026.",
                          known={"read", "the"})
        for phrase in ("three thirty P M", "twenty five percent", "twelve dollars and fifty cents",
                       "one thousand five hundred users", "P D F", "June twelfth, twenty twenty six"):
            self.assertIn(phrase, said)
        self.assertNotRegex(said, r"\d")

    def test_words_and_limits(self):
        self.assertEqual(nv.number_words(0), "zero")
        self.assertEqual(nv.number_words(101), "one hundred one")
        self.assertEqual(nv.number_words(3_000_000), "three million")
        self.assertEqual(nv.ordinal_words(21), "twenty first")
        self.assertEqual(nv.ordinal_words(40), "fortieth")
        self.assertNotIn("\t", nv.prepare("tab\tseparated"))
        self.assertLessEqual(len(nv.prepare("a" * 5000)), nv.MAX_TEXT)

    def test_smart_punctuation_units_currency_and_compounds(self):
        said = nv.prepare("I’m at the café — it’s 72°F, v2.6 uses 16GB and 500ms, $1 or €20, read/write, hands-free, -5°C.",
                          known={"cafe", "hands", "free"})
        for phrase in (
            "I'm at the cafe", "seventy two degrees Fahrenheit", "version two point six",
            "sixteen gigabytes", "five hundred milliseconds", "one dollar", "twenty euros",
            "read or write", "hands free", "minus five degrees Celsius",
        ):
            self.assertIn(phrase, said)
        self.assertNotIn("’", said)
        self.assertNotIn("—", said)
        self.assertEqual(nv.prepare("Meet at 12:00 pm."), "Meet at twelve P M.")


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def voice(self, data, helper=lambda: Path("pipkin-tts")):
        pack = {**nv.PACK, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        patcher = mock.patch.dict(nv.PACK, pack)
        patcher.start()
        self.addCleanup(patcher.stop)
        return nv.NaturalVoice(self.root, helper=helper, opener=lambda url: Response(data), runner=FakeHelper)

    def wait(self, voice):
        for _ in range(300):
            if (voice.status()["download"] or {}).get("state") != "downloading":
                return voice.status()
            time.sleep(0.01)
        self.fail("download did not finish")

    def test_download_keeps_only_needed_files(self):
        voice = self.voice(make_pack())
        voice.start_download()
        status = self.wait(voice)
        self.assertIsNone(status["download"])
        self.assertTrue(status["installed"])
        kept = sorted(p.name for p in voice.folder.iterdir())
        self.assertEqual(kept, sorted(nv.KEEP))
        self.assertFalse((voice.folder / "espeak-ng-data").exists())
        self.assertEqual([p.name for p in self.root.iterdir()], [nv.PACK["name"]])

    def test_bad_checksum_and_unsafe_members(self):
        data = make_pack()
        voice = self.voice(data)
        with mock.patch.dict(nv.PACK, {"sha256": "0" * 64}):
            voice.start_download()
            self.assertIn("checksum", self.wait(voice)["download"]["error"])
        self.assertFalse(voice.installed())
        # A symlink posing as a needed file is refused.
        tricky = io.BytesIO()
        with tarfile.open(fileobj=tricky, mode="w:bz2") as tar:
            info = tarfile.TarInfo(f"{nv.PACK['name']}/model.int8.onnx")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        archive = self.root / "tricky.tar.bz2"
        archive.write_bytes(tricky.getvalue())
        with self.assertRaisesRegex(ValueError, "unexpected file"):
            nv.extract_pack(archive, self.root / "out")

    def test_missing_files_are_an_error(self):
        archive = self.root / "partial.tar.bz2"
        archive.write_bytes(make_pack(skip=("voices.bin",)))
        with self.assertRaisesRegex(ValueError, "missing"):
            nv.extract_pack(archive, self.root / "out")

    def test_duplicate_download_and_cancel_during_extraction(self):
        voice = self.voice(make_pack())
        entered = threading.Event()
        release = threading.Event()
        original = nv.extract_pack

        def delayed_extract(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        with mock.patch.object(nv, "extract_pack", side_effect=delayed_extract):
            try:
                voice.start_download()
                self.assertTrue(entered.wait(2))
                second = threading.Thread(target=voice.start_download, daemon=True)
                second.start()
                second.join(1)
                self.assertFalse(second.is_alive(), "duplicate download must not deadlock")
                with self.assertRaisesRegex(ValueError, "Cancel the download"):
                    voice.remove()
                voice.cancel_download()
            finally:
                release.set()
            self.assertFalse(self.wait(voice)["installed"])

    def test_needs_the_engine(self):
        voice = self.voice(make_pack(), helper=lambda: None)
        with self.assertRaisesRegex(ValueError, "engine"):
            voice.start_download()
        self.assertFalse(voice.status()["engine"])

    def test_redirects_stay_on_github(self):
        handler = nv._LimitedRedirect()
        request = urllib.request.Request(nv.PACK["url"])
        self.assertIsNotNone(handler.redirect_request(request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/x"))
        for target in ("http://github.com/x", "https://evil.example/x", "https://githubusercontent.com.evil.example/x"):
            with self.assertRaises(urllib.error.URLError):
                handler.redirect_request(request, None, 302, "Found", {}, target)


class SpeakTest(PackTest):
    def installed_voice(self):
        voice = self.voice(make_pack())
        voice.start_download()
        self.wait(voice)
        return voice

    def test_speaks_through_one_long_lived_helper(self):
        voice = self.installed_voice()
        first = voice.speak("Hello world at 3 pm.", "heart", 1.0)
        second = voice.speak("Hello again.", "bella", 1.2)
        self.assertTrue(first.startswith(b"RIFF") and second.startswith(b"RIFF"))
        helper = voice._process
        self.assertEqual(len(helper.requests), 2)
        sid, speed, _path, text = helper.requests[0].rstrip("\n").split("\t", 3)
        self.assertEqual((sid, speed, text), ("3", "1.00", "Hello world at three P M."))
        self.assertEqual(helper.requests[1].split("\t")[:2], ["2", "1.20"])
        self.assertIn(str(voice.folder / "lexicon-us-en.txt"), helper.command)
        self.assertEqual(list(voice.root.glob("say-*")), [])

    def test_british_voice_restarts_with_the_british_dictionary(self):
        voice = self.installed_voice()
        (voice.folder / "lexicon-gb-en.txt").write_text("hello h e l o\n")
        voice.speak("Hello.", "heart")
        voice.speak("Hello.", "emma")
        self.assertIn(str(voice.folder / "lexicon-gb-en.txt"), voice._process.command)

    def test_rejects_empty_and_unknown_values(self):
        voice = self.installed_voice()
        with self.assertRaises(ValueError):
            voice.speak("   ")
        with self.assertRaises(ValueError):
            voice.speak("...!!!")
        voice.speak("Hello.", "no-such-voice", 99)
        self.assertEqual(voice._process.requests[-1].split("\t")[:2], ["3", "1.00"])

    def test_malformed_requests_do_not_reach_the_helper(self):
        voice = self.installed_voice()
        for text in ("a" * (nv.MAX_TEXT + 1), "bad\x00text", "\ud800"):
            with self.subTest(text=repr(text)), self.assertRaises(ValueError):
                voice.speak(text)
        self.assertIsNone(voice._process)
        voice.speak("Hello.", [], True)
        self.assertEqual(voice._process.requests[-1].split("\t")[:2], ["3", "1.00"])

    def test_helper_read_timeout_is_bounded(self):
        release = threading.Event()
        process = mock.Mock()
        process.stdout.readline.side_effect = lambda: release.wait(2) and ""
        process.wait.side_effect = subprocess.TimeoutExpired("helper", 2)
        try:
            with self.assertRaisesRegex(ValueError, "timed out"):
                nv.NaturalVoice._read_reply(process, 0.02)
            process.kill.assert_called_once()
        finally:
            release.set()

    def test_idle_helper_is_closed_and_remove_cleans_up(self):
        voice = self.installed_voice()
        voice.speak("Hello.")
        voice._last_used -= nv.IDLE_SECONDS + 1
        voice.close_if_idle()
        self.assertIsNone(voice._process)
        voice.remove()
        self.assertFalse(voice.installed())

    def test_warm_starts_the_helper_ahead_of_time(self):
        voice = self.installed_voice()
        voice.warm("emma")
        for _ in range(100):
            if voice._process:
                break
            time.sleep(0.01)
        self.assertEqual(voice._accent, "gb")
        self.assertIn("gb", voice._known)

    def test_needs_the_pack(self):
        voice = self.voice(make_pack())
        with self.assertRaisesRegex(ValueError, "Download the natural voice"):
            voice.speak("Hello.")

    def test_sentence_cache_and_cancel_check(self):
        voice = self.installed_voice()
        first = voice.speak("Hello world.", "heart", 1.0)
        second = voice.speak("Hello world.", "heart", 1.0)
        self.assertEqual(first, second)
        self.assertEqual(len(voice._process.requests), 1, "repeat phrase should hit the LRU cache")
        self.assertGreater(voice.status()["diskBytes"], 0)

        # Cancellation check aborts before touching the helper.
        class Cancelled(Exception):
            pass

        with self.assertRaises(Cancelled):
            voice.speak("Another sentence.", "heart", 1.0, check=lambda: (_ for _ in ()).throw(Cancelled()))
        self.assertEqual(len(voice._process.requests), 1)
        voice.close()
        self.assertIsNone(voice._process)


class VoiceFrontendTest(unittest.TestCase):
    def test_chunking_preserves_text_and_bounds_requests(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        script = """
const assert = require("node:assert/strict");
global.window = { PipkinUI: {} };
eval(require("fs").readFileSync("dist/voice.js", "utf8"));
const { speechChunks, speakable } = window.PipkinVoice;
assert.deepEqual(speechChunks("Hello. Next sentence", false), ["Hello."]);
assert.deepEqual(speechChunks("Hello.", false), []);
assert.deepEqual(speechChunks("Value is 3.14. Next", false), ["Value is 3.14."]);
assert.deepEqual(speechChunks("Dr. Smith met Prof. Lee at 3 p.m. today. Next", false), ["Dr. Smith met Prof. Lee at 3 p.m. today."]);
assert.deepEqual(speechChunks("你好。再见！", true), ["你好。", "再见！"]);
assert.equal(speakable("Hi 😊 $$x^2$$\\n---\\n**bold**").trim(), "Hi (the formula is on screen) bold");
for (const text of ["x".repeat(1501), "a".repeat(499) + "🙂" + "b".repeat(700), "Word ".repeat(400)]) {
  const chunks = speechChunks(text, true);
  assert.equal(chunks.join(""), text);
  assert.ok(chunks.every(c => c.length <= 500));
  assert.ok(chunks.every(c => !/[\\uD800-\\uDBFF]$/.test(c) && !/^[\\uDC00-\\uDFFF]/.test(c)));
}
"""
        subprocess.run(["node", "-e", script], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
