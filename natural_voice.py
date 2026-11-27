"""Natural voice for conversation mode: Kokoro (Apache 2.0) run on this device by pipkin-tts, a small helper built
from sherpa-onnx (Apache 2.0) that keeps the model loaded. The voice pack is downloaded only when the user asks,
pinned by URL, size and SHA-256, unpacked without following links or leaving its folder, and trimmed to the files the
voice needs. Text never leaves the device."""
from collections import OrderedDict
import hashlib
import os
import queue
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PACK = {
    "name": "kokoro-int8-multi-lang-v1_0",
    "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_0.tar.bz2",
    "size": 132353410,
    "sha256": "72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653",
}
# Only these files are kept from the pack (espeak-ng data and Chinese front-end files are not used).
KEEP = ("model.int8.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-gb-en.txt", "LICENSE")
DOWNLOAD_HOSTS = ("github.com", ".githubusercontent.com")
# Speaker IDs from the model's own id2speaker table.
VOICES = {
    "heart": {"label": "Heart", "detail": "Warm · American", "sid": 3, "accent": "us"},
    "bella": {"label": "Bella", "detail": "Bright · American", "sid": 2, "accent": "us"},
    "nicole": {"label": "Nicole", "detail": "Soft · American", "sid": 6, "accent": "us"},
    "michael": {"label": "Michael", "detail": "Calm · American", "sid": 16, "accent": "us"},
    "fenrir": {"label": "Fenrir", "detail": "Deep · American", "sid": 14, "accent": "us"},
    "puck": {"label": "Puck", "detail": "Lively · American", "sid": 18, "accent": "us"},
    "emma": {"label": "Emma", "detail": "Clear · British", "sid": 21, "accent": "gb"},
    "george": {"label": "George", "detail": "Steady · British", "sid": 26, "accent": "gb"},
}
DEFAULT_VOICE = "heart"
MAX_TEXT = 600
IDLE_SECONDS = 180


def helper_path():
    override = os.environ.get("PIPKIN_TTS")
    if override and Path(override).is_file():
        return Path(override)
    name = "pipkin-tts.exe" if os.name == "nt" else "pipkin-tts"
    root = Path(__file__).resolve().parent
    for path in (root / name, root / "build" / "voice-runtime" / name):
        if path.is_file():
            return path
    return None


class _LimitedRedirect(urllib.request.HTTPRedirectHandler):
    """The voice pack may only redirect to GitHub's own HTTPS download hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(host == h or (h.startswith(".") and host.endswith(h)) for h in DOWNLOAD_HOSTS):
            raise urllib.error.URLError("Unexpected download location.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(), _LimitedRedirect())
    return opener.open(urllib.request.Request(url, headers={"User-Agent": "Pipkin"}), timeout=30)


# ----- text preparation: the voice skips words it can't pronounce, so say numbers and acronyms in words -----
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
_LETTERS = {c: c for c in "abcdefghijklmnopqrstuvwxyz"}


def number_words(n):
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + number_words(n % 100))
    for size, word in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            return number_words(n // size) + " " + word + ("" if rest == 0 else " " + number_words(rest))
    return str(n)


def ordinal_words(n):
    words = number_words(n).split(" ")
    last = words[-1]
    if last in _ORDINAL:
        words[-1] = _ORDINAL[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] = last + "th"
    return " ".join(words)


def _year(n):
    if 2000 <= n <= 2009:
        return number_words(n)
    return number_words(n // 100) + " " + ("hundred" if n % 100 == 0 else ("oh " + _ONES[n % 100]) if n % 100 < 10 else number_words(n % 100))


_UNIT_WORDS = {
    "ms": "milliseconds", "sec": "seconds", "secs": "seconds", "min": "minutes", "mins": "minutes",
    "hr": "hours", "hrs": "hours", "kb": "kilobytes", "mb": "megabytes", "gb": "gigabytes", "tb": "terabytes",
    "khz": "kilohertz", "mhz": "megahertz", "ghz": "gigahertz", "hz": "hertz",
    "km": "kilometers", "cm": "centimeters", "mm": "millimeters", "kg": "kilograms", "mg": "milligrams",
    "mph": "miles per hour", "fps": "frames per second", "px": "pixels",
}


def _currency(symbol, whole_str, cents_str):
    names = {"$": ("dollar", "dollars", "cent", "cents"),
             "€": ("euro", "euros", "cent", "cents"),
             "£": ("pound", "pounds", "penny", "pence")}
    sg, pl, c_sg, c_pl = names[symbol]
    whole = int(whole_str)
    out = number_words(whole) + " " + (sg if whole == 1 else pl)
    if cents_str and int(cents_str):
        cents = int(cents_str)
        out += f" and {number_words(cents)} {c_sg if cents == 1 else c_pl}"
    return out


def _strip_accents(text):
    # Normalize Latin diacritics (e.g. café -> cafe, naïve -> naive) so English Kokoro speaks them instead of skipping.
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def prepare(text, known=None):
    """Spell out numbers, times, money, units, punctuation and acronyms so every word can be spoken clearly."""
    text = str(text or "")
    # Normalize smart punctuation before lexicon lookup (Kokoro lexicons use ASCII apostrophe `'`).
    text = (text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2014", ", ").replace("\u2013", ", ").replace("\u2026", ", "))
    text = _strip_accents(text)
    text = " ".join(text.replace("\t", " ").split())[:MAX_TEXT]
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,500 -> 1500
    text = re.sub(r"([\$€£])(\d+)(?:\.(\d{2}))?", lambda m: _currency(m[1], m[2], m[3]), text)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*([FC])\b",
                  lambda m: m[1] + (" degrees Fahrenheit" if m[2].upper() == "F" else " degrees Celsius"), text, flags=re.I)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*°(?!\w)", r"\1 degrees", text)
    text = re.sub(r"(-?\d+(?:\.\d+)?)%", lambda m: m[1] + " percent", text)
    text = re.sub(r"\bv(\d+(?:\.\d+)+)\b", lambda m: "version " + m[1], text, flags=re.I)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(ms|secs?|mins?|hrs?|kb|mb|gb|tb|khz|mhz|ghz|hz|km|cm|mm|kg|mg|mph|fps|px)\b",
                  lambda m: m[1] + " " + _UNIT_WORDS[m[2].lower()], text, flags=re.I)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\s*([ap])\.?m\.?\b", lambda m: _clock(m[1], m[2], True) + " " + m[3].upper() + " M", text, flags=re.I)
    text = re.sub(r"\b(\d{1,2})\s*([ap])\.?m\.?\b", lambda m: number_words(int(m[1])) + " " + m[2].upper() + " M", text, flags=re.I)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: _clock(m[1], m[2], False), text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: ordinal_words(int(m[1])), text, flags=re.I)
    text = re.sub(r"\b(1[2-9]\d\d|20\d\d)\b(?=\s*(?:,|\.|\)|$|\s+(?:and|to|through|was|is|in|when|until)\b))",
                  lambda m: _year(int(m[1])), text)
    text = re.sub(r"(?<!\w)-(\d+\.\d+)\b", lambda m: "minus " + " point ".join([
        number_words(int(m[1].split(".")[0])), " ".join(_ONES[int(d)] for d in m[1].split(".")[1])]), text)
    text = re.sub(r"\b\d+\.\d+\b", lambda m: " point ".join([number_words(int(m[0].split(".")[0])),
                                                               " ".join(_ONES[int(d)] for d in m[0].split(".")[1])]), text)
    text = re.sub(r"(?<!\w)-(\d{1,12})\b", lambda m: "minus " + number_words(int(m[1])), text)
    text = re.sub(r"\b\d{1,12}\b", lambda m: number_words(int(m[0])), text)
    # Split slash-joined words (read/write, 24/7), underscores, and unknown hyphenated compounds so Kokoro never drops them.
    text = re.sub(r"(?<=[A-Za-z])/(?=[A-Za-z])", " or ", text)
    text = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", " ", text)
    text = re.sub(r"\b([A-Za-z]+)-([A-Za-z]+)\b",
                  lambda m: m[0] if known is not None and m[0].lower() in known else f"{m[1]} {m[2]}", text)
    # Acronyms (PDF, AI, URL) and unknown short ALL-CAPS tokens are read letter by letter.
    def word(m):
        w = m[0]
        lower = w.lower()
        if w.isupper() and 2 <= len(w) <= 5 and (known is None or lower not in known):
            return " ".join(w)
        return w
    text = re.sub(r"\b[A-Za-z]{2,}\b", word, text)
    text = (text.replace("&", " and ").replace("+", " plus ").replace("@", " at ")
            .replace("×", " times ").replace("÷", " divided by ").replace("≈", " about "))
    return " ".join(text.split()).strip()


def _clock(hours, minutes, has_meridiem=False):
    h, m = int(hours), int(minutes)
    if m == 0:
        if has_meridiem:
            return number_words(h)
        return number_words(h) + " o'clock" if h <= 12 else number_words(h) + " hundred"
    return number_words(h) + " " + ("oh " + _ONES[m] if m < 10 else number_words(m))


class NaturalVoice:
    def __init__(self, root, *, helper=helper_path, opener=_open, runner=subprocess.Popen):
        self.root = Path(root)
        self._helper = helper
        self._open = opener
        self._runner = runner
        self._lock = threading.RLock()       # download state
        self._speak = threading.Lock()      # one sentence at a time
        self._download = None
        self._process = None
        self._accent = None
        self._last_used = 0.0
        self._known = {}
        self._cache = OrderedDict()
        self._cache_bytes = 0

    @property
    def folder(self):
        return self.root / PACK["name"]

    def installed(self):
        return all((self.folder / name).is_file() for name in KEEP if name != "LICENSE")

    def disk_bytes(self):
        if not self.installed():
            return 0
        total = 0
        for name in KEEP:
            p = self.folder / name
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    def status(self):
        with self._lock:
            download = dict(self._download) if self._download else None
        if download:
            download.pop("cancel", None)
        return {"engine": self._helper() is not None, "installed": self.installed(), "size": PACK["size"],
                "diskBytes": self.disk_bytes(),
                "download": download, "voices": [{"id": key, **{k: v for k, v in info.items() if k in ("label", "detail")}}
                                                for key, info in VOICES.items()], "default": DEFAULT_VOICE}

    # ----- download -----
    def start_download(self):
        if self._helper() is None:
            raise ValueError("This build of Pipkin doesn't include the natural voice engine.")
        with self._lock:
            if self.installed():
                return self.status()
            if self._download and self._download["state"] == "downloading":
                return self.status()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if shutil.disk_usage(self.root).free < PACK["size"] * 3 + 200_000_000:
                raise ValueError("Not enough free disk space for the natural voice (about 400 MB while installing).")
            cancel = threading.Event()
            self._download = {"state": "downloading", "received": 0, "size": PACK["size"], "error": None, "cancel": cancel}
        threading.Thread(target=self._fetch, args=(cancel,), daemon=True).start()
        return self.status()

    def _fetch(self, cancel):
        part = self.root / (PACK["name"] + ".tar.bz2.part")
        staging = None
        error = None
        try:
            digest = hashlib.sha256()
            received = 0
            with self._open(PACK["url"]) as response, open(part, "wb") as out:
                length = response.headers.get("Content-Length")
                if length is not None and length.isdigit() and int(length) != PACK["size"]:
                    raise ValueError("The download is not the expected size.")
                while True:
                    if cancel.is_set():
                        raise InterruptedError()
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > PACK["size"]:
                        raise ValueError("The download is larger than expected.")
                    digest.update(chunk)
                    out.write(chunk)
                    with self._lock:
                        if self._download:
                            self._download["received"] = received
            if received != PACK["size"] or digest.hexdigest() != PACK["sha256"]:
                raise ValueError("The download didn't match its checksum, so it was discarded.")
            staging = Path(tempfile.mkdtemp(prefix="voice-", dir=self.root))
            extract_pack(part, staging)
            with self._lock:
                if cancel.is_set():
                    raise InterruptedError()
                shutil.rmtree(self.folder, ignore_errors=True)
                os.replace(staging / PACK["name"], self.folder)
        except InterruptedError:
            pass
        except (OSError, ValueError, urllib.error.URLError, tarfile.TarError) as problem:
            error = str(problem) if isinstance(problem, ValueError) else "The download failed. Check your connection and try again."
        finally:
            part.unlink(missing_ok=True)
            if staging:
                shutil.rmtree(staging, ignore_errors=True)
            with self._lock:
                if error and not cancel.is_set():
                    self._download = {"state": "failed", "received": 0, "size": PACK["size"], "error": error}
                else:
                    self._download = None

    def cancel_download(self):
        with self._lock:
            if self._download:
                if self._download["state"] == "downloading":
                    self._download["cancel"].set()
                else:
                    self._download = None
        return self.status()

    def remove(self):
        with self._lock:
            if self._download and self._download["state"] == "downloading":
                raise ValueError("Cancel the download and wait for it to finish before removing the voice.")
            with self._speak:
                self._stop()
                self._known.clear()
                self._cache.clear()
                self._cache_bytes = 0
                shutil.rmtree(self.folder, ignore_errors=True)
        return self.status()

    def close(self):
        """Shut down the background helper cleanly (called on server exit)."""
        with self._speak:
            self._stop()

    # ----- speaking -----
    def _stop(self):
        process, self._process, self._accent = self._process, None, None
        if process:
            try:
                if process.poll() is None:
                    process.stdin.close()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                for pipe in (process.stdin, process.stdout):
                    try:
                        pipe.close()
                    except OSError:
                        pass

    @staticmethod
    def _read_reply(process, timeout):
        """Bound startup and inference time even if the native helper stalls."""
        result = queue.Queue(maxsize=1)

        def read():
            try:
                result.put(process.stdout.readline())
            except (OSError, ValueError):
                result.put("")

        threading.Thread(target=read, daemon=True).start()
        try:
            return result.get(timeout=timeout).strip()
        except queue.Empty:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise ValueError("The natural voice timed out. Try again.") from None

    def _known_words(self, accent):
        if accent not in self._known:
            words = set()
            with open(self.folder / f"lexicon-{accent}-en.txt", encoding="utf-8", errors="replace") as lexicon:
                for line in lexicon:
                    words.add(line.split(" ", 1)[0])
            self._known[accent] = words
        return self._known[accent]

    def _start(self, accent):
        helper = self._helper()
        if helper is None:
            raise ValueError("This build of Pipkin doesn't include the natural voice engine.")
        if not self.installed():
            raise ValueError("Download the natural voice in Settings › Voice first.")
        self._stop()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
        threads = str(max(2, min(6, (os.cpu_count() or 4) - 2)))
        process = self._runner(
            [str(helper), "--model", str(self.folder / "model.int8.onnx"), "--voices", str(self.folder / "voices.bin"),
             "--tokens", str(self.folder / "tokens.txt"), "--lexicon", str(self.folder / f"lexicon-{accent}-en.txt"),
             "--threads", threads],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            cwd=self.folder, creationflags=flags)
        self._process, self._accent = process, accent
        try:
            ready = self._read_reply(process, 30)
            if not ready.startswith("ready "):
                raise ValueError("The natural voice couldn't start.")
        except (ValueError, OSError):
            self._stop()
            raise

    def _cache_put(self, key, wav):
        if len(wav) > 2_000_000:
            return
        if key in self._cache:
            self._cache_bytes -= len(self._cache.pop(key))
        self._cache[key] = wav
        self._cache_bytes += len(wav)
        while len(self._cache) > 16 or self._cache_bytes > 8_000_000:
            _, evicted = self._cache.popitem(last=False)
            self._cache_bytes -= len(evicted)

    def speak(self, text, voice=DEFAULT_VOICE, speed=1.0, check=lambda: None):
        """Return WAV bytes for one sentence. Checks cancellation while waiting for the lock."""
        voice_id = voice if isinstance(voice, str) and voice in VOICES else DEFAULT_VOICE
        info = VOICES[voice_id]
        if type(speed) not in (int, float) or not 0.6 <= speed <= 1.6:
            speed = 1.0
        speed_key = round(float(speed), 2)
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise ValueError("Nothing to say.")
        if len(text) > MAX_TEXT:
            raise ValueError("Speech text exceeds the sentence limit.")
        try:
            text.encode("utf-8")
        except UnicodeError:
            raise ValueError("Speech text contains invalid characters.") from None
        if any(char in str(self.root) for char in "\t\r\n"):
            raise ValueError("The speech folder has an unsupported path.")
        deadline = time.monotonic() + 20.0
        while not self._speak.acquire(timeout=0.05):
            check()
            if time.monotonic() >= deadline:
                raise ValueError("The natural voice is busy.")
        try:
            check()
            if self._process is None or self._process.poll() is not None or self._accent != info["accent"]:
                self._start(info["accent"])
            spoken = prepare(text, self._known_words(info["accent"]))
            if not re.search(r"[A-Za-z]", spoken):
                raise ValueError("Nothing to say.")
            cache_key = (voice_id, speed_key, spoken)
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                self._last_used = time.monotonic()
                return cached
            check()
            with tempfile.TemporaryDirectory(prefix="say-", dir=self.root) as folder:
                out = Path(folder) / "speech.wav"
                self._process.stdin.write(f"{info['sid']}\t{speed_key:.2f}\t{out}\t{spoken}\n")
                self._process.stdin.flush()
                reply = self._read_reply(self._process, 45)
                if not reply.startswith("ok ") or not out.is_file():
                    if not reply:
                        self._stop()
                    raise ValueError("The natural voice couldn't say that.")
                self._last_used = time.monotonic()
                if out.stat().st_size > 20_000_000:
                    raise ValueError("Generated audio exceeds the size limit.")
                wav = out.read_bytes()
                self._cache_put(cache_key, wav)
                return wav
        except ValueError:
            if self._process and self._process.poll() is not None:
                self._stop()
            raise
        except (OSError, BrokenPipeError):
            self._stop()
            raise ValueError("The natural voice stopped unexpectedly. It will restart on the next sentence.") from None
        finally:
            self._speak.release()

    def warm(self, voice=DEFAULT_VOICE):
        """Start the helper and load the dictionary in the background, so the first sentence isn't slow."""
        info = VOICES.get(voice, VOICES[DEFAULT_VOICE]) if isinstance(voice, str) else VOICES[DEFAULT_VOICE]

        def run():
            if not self.installed() or not self._speak.acquire(blocking=False):
                return
            try:
                if self._process is None or self._process.poll() is not None or self._accent != info["accent"]:
                    self._start(info["accent"])
                self._known_words(info["accent"])
                self._last_used = time.monotonic()
            except (OSError, ValueError):
                self._stop()
            finally:
                self._speak.release()

        threading.Thread(target=run, daemon=True).start()

    def close_if_idle(self):
        """Free the model's memory after a few quiet minutes."""
        if self._process and time.monotonic() - self._last_used > IDLE_SECONDS and self._speak.acquire(blocking=False):
            try:
                self._stop()
            finally:
                self._speak.release()


def extract_pack(archive, destination):
    """Unpack only the files the voice needs, as plain files inside the pack folder (no links, no escaping paths)."""
    destination = Path(destination)
    wanted = {f"{PACK['name']}/{name}" for name in KEEP}
    with tarfile.open(archive, "r:bz2") as tar:
        found = set()
        for member in tar:
            if member.name not in wanted:
                continue
            if member.name in found or not member.isfile() or not 0 < member.size <= 400_000_000:
                raise ValueError("The voice pack contains an unexpected file.")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
            found.add(member.name)
    missing = wanted - found - {f"{PACK['name']}/LICENSE"}
    if missing:
        raise ValueError("The voice pack is missing files.")
