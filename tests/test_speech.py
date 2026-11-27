import hashlib
import io
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from unittest import mock

import speech


class FakeResponse(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeProcess:
    """Stands in for whisper-cli: writes result.txt where -of points, like the real engine."""

    def __init__(self, command, text="Hello there.", code=0, delay=0.0, record=None, **kwargs):
        self.command, self.kwargs = command, kwargs
        self.returncode = None
        self.killed = False
        self._done_at = time.monotonic() + delay
        self._text, self._code = text, code
        if record is not None:
            record.append(self)
        wav = Path(command[command.index("-f") + 1])
        assert wav.is_file()
        with wave.open(str(wav)) as audio:
            assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 16000, 2)
        self.folder = wav.parent

    def poll(self):
        if self.returncode is None and time.monotonic() >= self._done_at:
            if self._code == 0 and self._text is not None:
                Path(self.command[self.command.index("-of") + 1] + ".txt").write_text(self._text, "utf-8")
            self.returncode = self._code
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class SpeechTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "speech"
        self.engine = Path(self.tmp.name) / "whisper-cli"
        self.engine.write_text("")
        self.processes = []
        self.process_options = {}
        self.speech = speech.Speech(self.root, engine=lambda: self.engine,
                                    runner=lambda command, **kw: FakeProcess(command, record=self.processes, **{**kw, **self.process_options}))

    def install(self, model="base"):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.speech.model_path(model)
        with open(path, "wb") as out:
            out.truncate(speech.MODELS[model]["size"])

    def pcm(self, seconds=1.0):
        return b"\x10\x00" * int(16000 * seconds)

    def test_transcribes_and_deletes_the_audio(self):
        self.install()
        self.process_options = {"text": "  Hello [BLANK_AUDIO] there. (music)\n"}
        self.assertEqual(self.speech.transcribe(self.pcm(), "base", "auto"), "Hello there.")
        command = self.processes[0].command
        self.assertEqual(command[command.index("-l") + 1], "auto")
        self.assertIn("-np", command)
        self.assertFalse(self.processes[0].folder.exists(), "the recording must be deleted after transcription")
        self.assertEqual(list(self.root.glob("dictation-*")), [])

    def test_english_model_always_uses_english(self):
        self.install("base.en")
        self.speech.transcribe(self.pcm(), "base.en", "zh")
        command = self.processes[0].command
        self.assertEqual(command[command.index("-l") + 1], "en")

    def test_rejects_bad_input_before_running_anything(self):
        self.install()
        for pcm in (b"", b"\x00", b"\x00\x00" * (speech.MAX_PCM_BYTES // 2 + 1)):
            with self.assertRaises(ValueError):
                self.speech.transcribe(pcm, "base")
        with self.assertRaises(ValueError):
            self.speech.transcribe(self.pcm(), "base", "klingon")
        with self.assertRaises(ValueError):
            self.speech.transcribe(self.pcm(), "turbo")  # not installed
        self.assertEqual(self.processes, [])

    def test_missing_engine(self):
        self.install()
        quiet = speech.Speech(self.root, engine=lambda: None)
        with self.assertRaisesRegex(ValueError, "speech engine"):
            quiet.transcribe(self.pcm(), "base")
        self.assertFalse(quiet.status()["engine"])

    def test_engine_failure_and_cancel_kill_the_process(self):
        self.install()
        self.process_options = {"code": 3}
        with self.assertRaisesRegex(ValueError, "couldn't transcribe"):
            self.speech.transcribe(self.pcm(), "base")
        self.process_options = {"delay": 30}

        def stop():
            raise InterruptedError()

        with self.assertRaises(InterruptedError):
            self.speech.transcribe(self.pcm(), "base", check=stop)
        self.assertTrue(self.processes[-1].killed)
        self.assertEqual(list(self.root.glob("dictation-*")), [])

    def test_one_transcription_at_a_time(self):
        self.install()
        self.process_options = {"delay": 0.5}
        first = threading.Thread(target=self.speech.transcribe, args=(self.pcm(), "base"))
        first.start()
        time.sleep(0.15)
        with self.assertRaises(speech.BusyError):
            self.speech.transcribe(self.pcm(), "base")
        first.join()

    def test_windows_runs_the_engine_without_a_console(self):
        self.install()
        with mock.patch.object(speech, "no_window_flags", return_value=0x08000000):
            self.speech.transcribe(self.pcm(), "base")
        self.assertEqual(self.processes[0].kwargs["creationflags"], 0x08000000)
        with mock.patch.object(speech.os, "name", "nt"):
            self.assertEqual(speech.no_window_flags(), 0x08000000)
        with mock.patch.object(speech.os, "name", "posix"):
            self.assertEqual(speech.no_window_flags(), 0)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = b"model-bytes" * 1000
        self.model = {"label": "Test", "detail": "", "file": "test.bin", "size": len(self.data),
                      "sha256": hashlib.sha256(self.data).hexdigest(), "english_only": False}
        patcher = mock.patch.dict(speech.MODELS, {"test": self.model})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.urls = []

    def make(self, payload=None, length=None):
        def opener(url):
            self.urls.append(url)
            return FakeResponse(self.data if payload is None else payload, length)
        return speech.Speech(self.root, engine=lambda: Path("whisper-cli"), opener=opener)

    def wait(self, s):
        for _ in range(200):
            if not (s.status()["download"] or {}).get("state") == "downloading":
                return s.status()
            time.sleep(0.01)
        self.fail("download did not finish")

    def test_verified_download_lands_atomically(self):
        s = self.make()
        s.start_download("test")
        status = self.wait(s)
        self.assertIsNone(status["download"])
        self.assertTrue(s.installed("test"))
        self.assertEqual(self.urls, [speech.MODEL_URL + "test.bin"])
        self.assertIn(speech.MODEL_REVISION, self.urls[0])
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_wrong_checksum_is_discarded(self):
        s = self.make(payload=b"X" * len(self.data))
        s.start_download("test")
        status = self.wait(s)
        self.assertEqual(status["download"]["state"], "failed")
        self.assertIn("checksum", status["download"]["error"])
        self.assertFalse(s.installed("test"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oversized_download_is_refused(self):
        s = self.make(length=len(self.data) + 1)
        s.start_download("test")
        self.assertEqual(self.wait(s)["download"]["state"], "failed")
        self.assertFalse(s.installed("test"))

    def test_remove_and_unknown_model(self):
        s = self.make()
        s.start_download("test")
        self.wait(s)
        s.remove("test")
        self.assertFalse(s.installed("test"))
        with self.assertRaises(ValueError):
            s.start_download("../../etc/passwd")

    def test_needs_the_engine(self):
        s = speech.Speech(self.root, engine=lambda: None, opener=lambda url: self.fail("must not download"))
        with self.assertRaisesRegex(ValueError, "speech engine"):
            s.start_download("test")

    def test_redirects_stay_on_hugging_face_https(self):
        handler = speech._LimitedRedirect()
        request = urllib.request.Request(speech.MODEL_URL + "ggml-base.bin")
        for target in ("https://cdn-lfs-us-1.hf.co/repos/x", "https://cas-bridge.xethub.hf.co/x", "https://huggingface.co/x"):
            self.assertIsNotNone(handler.redirect_request(request, None, 302, "Found", {}, target))
        for target in ("http://cdn-lfs.hf.co/x", "https://evil.example/x", "https://hf.co.evil.example/x", "file:///etc/passwd"):
            with self.assertRaises(urllib.error.URLError):
                handler.redirect_request(request, None, 302, "Found", {}, target)


class CleanupGuardTest(unittest.TestCase):
    def test_accepts_punctuation_and_filler_removal(self):
        raw = "um so I think we should uh meet on friday at 3 to go over the report"
        self.assertTrue(speech.cleanup_keeps_meaning(raw, "I think we should meet on Friday at 3 to go over the report."))

    def test_rejects_lost_details(self):
        raw = "don't send the file to sam until june 12 at notes.md"
        self.assertFalse(speech.cleanup_keeps_meaning(raw, "Send the file to Sam until June 12 at notes.md."))  # lost negation
        self.assertFalse(speech.cleanup_keeps_meaning(raw, "Don't send the file to Sam until June 13 at notes.md."))  # changed number
        self.assertFalse(speech.cleanup_keeps_meaning(raw, "Don't send the file to Sam until June 12 at notes."))  # lost file name
        self.assertFalse(speech.cleanup_keeps_meaning(raw, "Don't send the file until June 12 at notes.md."))  # dropped a name

    def test_rejects_answers_instead_of_cleanup(self):
        raw = "what is the capital of france"
        self.assertFalse(speech.cleanup_keeps_meaning(raw, "The capital of France is Paris, a city famous for the Eiffel Tower."))
        self.assertFalse(speech.cleanup_keeps_meaning(raw, ""))

    def test_accepts_cjk_and_contraction_cleanup(self):
        self.assertTrue(speech.cleanup_keeps_meaning("嗯你好世界今天天气不错", "你好世界，今天天气不错。"))
        self.assertTrue(speech.cleanup_keeps_meaning("こんにちは今日はいい天気ですね", "こんにちは、今日はいい天気ですね。"))
        self.assertFalse(speech.cleanup_keeps_meaning("明天下午三点开会", "好的，我已经帮您记下了明天下午三点的会议。"))
        self.assertTrue(speech.cleanup_keeps_meaning("um do not forget it is due tomorrow", "Don't forget, it's due tomorrow."))
        self.assertTrue(speech.cleanup_keeps_meaning("don’t merge before friday", "Don't merge before Friday."))

    def test_filters_whisper_hallucinations_conservatively(self):
        self.assertEqual(speech.clean_transcript("Thank you for watching."), "")
        self.assertEqual(speech.clean_transcript("Subtitles by Amara.org"), "")
        self.assertEqual(speech.clean_transcript("Thank you.", quiet=True), "")
        self.assertEqual(speech.clean_transcript("Thank you.", quiet=False), "Thank you.")
        self.assertEqual(speech.clean_transcript("hello you you you you you"), "hello you")


if __name__ == "__main__":
    unittest.main()
