"""Cross-platform OS adapter for Pipkin (macOS + Windows + Linux fallback).

Keeps OS-specific paths, Ollama discovery, memory telemetry, sleep prevention,
PDF reading/rendering fallbacks, folder picking, and file/URL opening in one place
so server.py, agent.py, and artifacts.py run cleanly on both macOS and Windows.
"""

import ctypes
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import zlib


def app_data_dir(platform_name=None, env=None, home=None):
    """Return the per-user Pipkin data directory for the active OS."""
    plat = platform_name or sys.platform
    env_map = os.environ if env is None else env
    user_home = Path.home() if home is None else Path(home)
    if plat == "darwin":
        return user_home / "Library" / "Application Support" / "Pipkin"
    if plat == "win32":
        local_app = env_map.get("LOCALAPPDATA") or env_map.get("APPDATA")
        if local_app:
            return Path(local_app) / "Pipkin"
        return user_home / "AppData" / "Local" / "Pipkin"
    xdg = env_map.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "pipkin"
    return user_home / ".local" / "share" / "pipkin"


def ollama_candidates(platform_name=None, env=None):
    """Return ordered candidate paths for the local Ollama binary."""
    plat = platform_name or sys.platform
    env_map = os.environ if env is None else env
    candidates = [shutil.which("ollama")]
    if plat == "darwin":
        candidates.extend([
            "/opt/homebrew/bin/ollama",
            "/usr/local/bin/ollama",
            "/Applications/Ollama.app/Contents/Resources/ollama",
        ])
    elif plat == "win32":
        local_app = env_map.get("LOCALAPPDATA")
        prog_files = env_map.get("ProgramFiles")
        if local_app:
            candidates.append(str(Path(local_app) / "Programs" / "Ollama" / "ollama.exe"))
        if prog_files:
            candidates.append(str(Path(prog_files) / "Ollama" / "ollama.exe"))
    else:
        candidates.extend(["/usr/local/bin/ollama", "/usr/bin/ollama"])
    return [c for c in candidates if c]


def find_ollama_executable():
    for candidate in ollama_candidates():
        if Path(candidate).is_file():
            return candidate
    return None


def parse_windows_memory_status(memory_load, total_phys, avail_phys, total_page, avail_page):
    """Convert Win32 MEMORYSTATUSEX fields into Pipkin's telemetry schema."""
    total_ram = max(0, int(total_phys or 0))
    avail_ram = max(0, min(total_ram, int(avail_phys or 0)))
    used_ram = max(0, total_ram - avail_ram)
    load = max(0, min(100, int(memory_load or 0)))
    if not load and total_ram:
        load = min(100, round((used_ram / total_ram) * 100))
    pressure = "critical" if load >= 92 else "warning" if load >= 80 else "normal"
    swap_total = max(0, int(total_page or 0) - total_ram)
    swap_avail = max(0, int(avail_page or 0) - avail_ram)
    swap_used = max(0, swap_total - swap_avail)
    return {
        "pressure": pressure,
        "total_ram": total_ram,
        "memory": {
            "used": used_ram,
            "app": used_ram,
            "wired": 0,
            "compressed": 0,
            "cached": 0,
            "swap_total": swap_total,
            "swap_used": swap_used,
            "used_pct": load,
        },
    }


def windows_memory_telemetry():
    """Read live RAM and pagefile usage on Windows via GlobalMemoryStatusEx (no extra deps)."""
    if sys.platform != "win32":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return parse_windows_memory_status(
            stat.dwMemoryLoad, stat.ullTotalPhys, stat.ullAvailPhys,
            stat.ullTotalPageFile, stat.ullAvailPageFile,
        )
    except (AttributeError, OSError):
        return None


def open_url_in_os(url):
    """Open a local or HTTP URL in the OS default browser."""
    if sys.platform == "darwin":
        return subprocess.run(["/usr/bin/open", str(url)], check=False).returncode
    if sys.platform == "win32":
        os.startfile(str(url))  # type: ignore[attr-defined]
        return 0
    return subprocess.run(["xdg-open", str(url)], check=False).returncode


def open_file_in_os(target, *, reveal=False):
    """Open or reveal a local workspace file in Finder / File Explorer."""
    if sys.platform == "darwin":
        args = ["/usr/bin/open", "-R", str(target)] if reveal else ["/usr/bin/open", str(target)]
        subprocess.run(args, check=True, timeout=5, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    if sys.platform == "win32":
        if reveal:
            subprocess.run(
                ["explorer.exe", f"/select,{target}"],
                check=False, timeout=5,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            os.startfile(str(target))  # type: ignore[attr-defined]
        return
    subprocess.run(["xdg-open", str(Path(target).parent if reveal else target)], check=True, timeout=5)


def pick_folder_windows(timeout=120):
    """Open Windows' native folder picker dialog via PowerShell STA WinForms.

    Returns (returncode, folder_path):
      0 -> folder chosen
      3 -> user cancelled (matches macOS choose-folder exit code)
      1 -> picker failed to launch
    """
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$owner = New-Object System.Windows.Forms.Form; "
        "$owner.TopMost = $true; $owner.StartPosition = 'CenterScreen'; "
        "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dlg.Description = 'Choose a workspace folder for Pipkin'; "
        "$dlg.UseDescriptionForTitle = $true; "
        "$dlg.ShowNewFolderButton = $true; "
        "$res = $dlg.ShowDialog($owner); "
        "$owner.Dispose(); "
        "if ($res -eq [System.Windows.Forms.DialogResult]::OK -and $dlg.SelectedPath) { "
        "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "  [Console]::Write($dlg.SelectedPath); exit 0 "
        "} else { exit 3 }"
    )
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=creation,
        )
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def kill_process_tree(pid, *, force=True):
    """Terminate a process and its children cleanly on both Windows and POSIX."""
    if sys.platform == "win32":
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        args = ["taskkill", "/T", "/PID", str(pid)]
        if force:
            args.insert(1, "/F")
        try:
            subprocess.run(
                args, check=False, timeout=5,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creation,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


class KeepAwake:
    """Prevent system sleep during long Agent runs on macOS and Windows."""

    def __init__(self):
        self._proc = None
        self._win_active = False

    def start(self):
        if sys.platform == "darwin":
            self._proc = subprocess.Popen(
                ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            self._win_active = True

    def stop(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                try:
                    self._proc.kill()
                except OSError:
                    pass
            self._proc = None
        if self._win_active and sys.platform == "win32":
            ES_CONTINUOUS = 0x80000000
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except (AttributeError, OSError):
                pass
            self._win_active = False


# ------------------------------------------------ cross-platform PDF utilities

_PDF_OBJ_RE = re.compile(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj\b", re.DOTALL)
_PDF_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.DOTALL)
_PDF_REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
_PDF_MAX_DECOMPRESSED = 16 * 1024 * 1024


def _decode_pdf_literal(raw):
    """Decode a PDF literal string (...) handling escapes and octal sequences."""
    out = bytearray()
    i = 0
    n = len(raw)
    escapes = {
        ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12,
        ord("("): ord("("), ord(")"): ord(")"), ord("\\"): ord("\\"),
    }
    while i < n:
        ch = raw[i]
        if ch == ord("\\") and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in (10, 13):
                i += 2
                if nxt == 13 and i < n and raw[i] == 10:
                    i += 1
                continue
            if nxt in escapes:
                out.append(escapes[nxt])
                i += 2
                continue
            if ord("0") <= nxt <= ord("7"):
                oct_end = min(n, i + 4)
                j = i + 1
                while j < oct_end and ord("0") <= raw[j] <= ord("7"):
                    j += 1
                out.append(int(raw[i + 1:j], 8) & 0xFF)
                i = j
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    if out.startswith(b"\xfe\xff"):
        return bytes(out[2:]).decode("utf-16-be", "replace")
    return bytes(out).decode("cp1252", "replace")


def _decode_pdf_hex(raw):
    cleaned = re.sub(rb"\s+", b"", raw)
    if len(cleaned) % 2:
        cleaned += b"0"
    try:
        data = bytes.fromhex(cleaned.decode("ascii"))
    except ValueError:
        return ""
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace")
    return data.decode("cp1252", "replace")


def _extract_text_from_content_stream(stream_bytes):
    """Parse PDF BT...ET text operators (Tj, TJ, ', \") from a decoded content stream."""
    lines = []
    cur = []
    pos = 0
    n = len(stream_bytes)

    def flush_line():
        text = "".join(cur).strip()
        cur.clear()
        if text:
            lines.append(text)

    while pos < n:
        bt = stream_bytes.find(b"BT", pos)
        if bt == -1:
            break
        et = stream_bytes.find(b"ET", bt + 2)
        if et == -1:
            et = n
        block = stream_bytes[bt + 2:et]
        pos = et + 2
        i = 0
        blen = len(block)
        while i < blen:
            ch = block[i]
            if ch == ord("("):
                depth = 1
                start = i + 1
                i += 1
                while i < blen and depth > 0:
                    if block[i] == ord("\\"):
                        i += 2
                        continue
                    if block[i] == ord("("):
                        depth += 1
                    elif block[i] == ord(")"):
                        depth -= 1
                    i += 1
                cur.append(_decode_pdf_literal(block[start:max(start, i - 1)]))
            elif ch == ord("<") and i + 1 < blen and block[i + 1] != ord("<"):
                end = block.find(b">", i + 1)
                if end == -1:
                    break
                cur.append(_decode_pdf_hex(block[i + 1:end]))
                i = end + 1
            elif ch == ord("["):
                i += 1
                parts = []
                while i < blen and block[i] != ord("]"):
                    if block[i] == ord("("):
                        depth = 1
                        start = i + 1
                        i += 1
                        while i < blen and depth > 0:
                            if block[i] == ord("\\"):
                                i += 2
                                continue
                            if block[i] == ord("("):
                                depth += 1
                            elif block[i] == ord(")"):
                                depth -= 1
                            i += 1
                        parts.append(_decode_pdf_literal(block[start:max(start, i - 1)]))
                    elif block[i] == ord("<") and i + 1 < blen and block[i + 1] != ord("<"):
                        end = block.find(b">", i + 1)
                        if end == -1:
                            break
                        parts.append(_decode_pdf_hex(block[i + 1:end]))
                        i = end + 1
                    elif block[i] == ord("-") or ord("0") <= block[i] <= ord("9"):
                        num_start = i
                        i += 1
                        while i < blen and (ord("0") <= block[i] <= ord("9") or block[i] == ord(".")):
                            i += 1
                        try:
                            kern = float(block[num_start:i])
                            if kern <= -180:
                                parts.append(" ")
                        except ValueError:
                            pass
                    else:
                        i += 1
                cur.append("".join(parts))
                i += 1
            elif block[i:i + 2] in (b"Td", b"TD", b"T*", b"Tm"):
                flush_line()
                i += 2
            else:
                i += 1
        flush_line()
    return "\n".join(lines)


def _decompress_stream(obj_body, budget):
    match = _PDF_STREAM_RE.search(obj_body)
    if not match:
        return b"", budget
    header = obj_body[:match.start()]
    raw = match.group(1)
    if b"/FlateDecode" in header:
        try:
            dobj = zlib.decompressobj()
            data = dobj.decompress(raw, budget)
            return data, max(0, budget - len(data))
        except zlib.error:
            return b"", budget
    return raw[:budget], max(0, budget - min(len(raw), budget))


def extract_pdf_pages_pure(data):
    """Pure-Python zero-dependency PDF text extractor returning '## PDF page N' sections."""
    if not isinstance(data, (bytes, bytearray)) or not data.startswith(b"%PDF-"):
        raise ValueError("Not a valid PDF file.")
    if b"/Encrypt" in data[:65536] or b"/Encrypt" in data[-65536:]:
        raise ValueError("Could not read this PDF. It may be password-protected.")

    objects = {}
    for m in _PDF_OBJ_RE.finditer(data):
        objects[int(m.group(1))] = m.group(2)

    budget = _PDF_MAX_DECOMPRESSED
    pages_text = []

    # Locate /Type /Page objects (excluding /Type /Pages)
    page_objs = []
    for obj_num in sorted(objects):
        body = objects[obj_num]
        if re.search(rb"/Type\s*/Page\b", body) and not re.search(rb"/Type\s*/Pages\b", body):
            page_objs.append(body)

    for body in page_objs:
        content_refs = []
        arr_match = re.search(rb"/Contents\s*\[(.*?)\]", body, re.DOTALL)
        if arr_match:
            content_refs = [int(r) for r in _PDF_REF_RE.findall(arr_match.group(1))]
        else:
            one_match = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", body)
            if one_match:
                content_refs = [int(one_match.group(1))]
        chunks = []
        for ref in content_refs:
            stream_obj = objects.get(ref)
            if not stream_obj or budget <= 0:
                continue
            decoded, budget = _decompress_stream(stream_obj, budget)
            text = _extract_text_from_content_stream(decoded)
            if text:
                chunks.append(text)
        pages_text.append("\n".join(chunks).strip())

    # Fallback: if no /Type /Page objects were parsed (e.g. object streams), scan streams directly
    if not any(pages_text):
        fallback = []
        for obj_num in sorted(objects):
            if budget <= 0:
                break
            decoded, budget = _decompress_stream(objects[obj_num], budget)
            if b"BT" in decoded:
                text = _extract_text_from_content_stream(decoded)
                if text:
                    fallback.append(text)
        if fallback:
            pages_text = fallback

    if not pages_text:
        raise ValueError("Could not extract readable text from this PDF.")

    sections = []
    for idx, page in enumerate(pages_text, 1):
        sections.append(f"## PDF page {idx}\n{page}" if page else f"## PDF page {idx}")
    return "\n\n".join(sections)


def extract_pdf_pages(data):
    """Extract page-numbered text from PDF bytes on Windows/Linux (optional pypdf/fitz or pure Python)."""
    if not isinstance(data, (bytes, bytearray)) or not data.startswith(b"%PDF-"):
        raise ValueError("Could not read this PDF. It may be password-protected or damaged.")
    # Prefer pypdf / PyPDF2 / PyMuPDF if installed in the user's Python environment.
    for mod_name in ("pypdf", "PyPDF2"):
        try:
            mod = __import__(mod_name)
            from io import BytesIO
            reader = mod.PdfReader(BytesIO(data))
            if getattr(reader, "is_encrypted", False):
                raise ValueError("Could not read this PDF. It may be password-protected.")
            out = []
            for idx, page in enumerate(reader.pages, 1):
                txt = (page.extract_text() or "").strip()
                out.append(f"## PDF page {idx}\n{txt}" if txt else f"## PDF page {idx}")
            if out:
                return "\n\n".join(out)
        except ImportError:
            continue
        except ValueError:
            raise
        except Exception:
            break
    try:
        import fitz  # type: ignore[import-not-found]
        doc = fitz.open(stream=data, filetype="pdf")
        out = []
        for idx, page in enumerate(doc, 1):
            txt = (page.get_text("text") or "").strip()
            out.append(f"## PDF page {idx}\n{txt}" if txt else f"## PDF page {idx}")
        if out:
            return "\n\n".join(out)
    except ImportError:
        pass
    except Exception:
        pass
    return extract_pdf_pages_pure(data)


_WINANSI_TRANS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "--", "\u2026": "...", "\u2022": "*",
    "\u00b7": "|", "\u00a0": " ", "\u200b": "", "\u2192": "->",
    "\u2190": "<-", "\u2264": "<=", "\u2265": ">=", "\u2260": "!=",
})


def _pdf_escape_text(text):
    clean = str(text or "").translate(_WINANSI_TRANS)
    encoded = clean.encode("cp1252", "replace").decode("cp1252")
    return encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf_blocks_pure(blocks):
    """Render Pipkin artifact blocks into a clean, multi-page %PDF-1.4 document with zero dependencies."""
    page_w, page_h = 612, 792
    margin_x, margin_top, margin_bottom = 56, 60, 56
    usable_w = page_w - 2 * margin_x

    pages = [[]]
    y = page_h - margin_top

    def ensure_space(needed):
        nonlocal y
        if y - needed < margin_bottom:
            pages.append([])
            y = page_h - margin_top

    styles = {
        "title": ("F2", 20, 26, 0, 10, (0.07, 0.14, 0.12)),
        "subtitle": ("F1", 11, 16, 0, 10, (0.28, 0.36, 0.33)),
        "heading": ("F2", 14, 20, 10, 4, (0.09, 0.24, 0.19)),
        "body": ("F1", 10.5, 15, 0, 6, (0.14, 0.18, 0.17)),
        "bullet": ("F1", 10.5, 15, 0, 4, (0.14, 0.18, 0.17)),
        "source": ("F1", 9, 13, 0, 8, (0.23, 0.39, 0.34)),
    }

    for block in blocks:
        kind = block.get("kind", "body") if isinstance(block, dict) else "body"
        text = str(block.get("text", "") if isinstance(block, dict) else block).strip()
        if not text:
            continue
        font, size, leading, before, after, (r, g, b) = styles.get(kind, styles["body"])
        indent = 14 if kind == "bullet" else 0
        # Approximate character width for Helvetica (~0.52 * font_size)
        max_chars = max(24, int((usable_w - indent) / (size * 0.52)))
        wrapped = []
        for para in text.splitlines():
            lines = textwrap.wrap(para.strip(), width=max_chars) or [""]
            wrapped.extend(lines)
        ensure_space(before + leading * min(len(wrapped), 3))
        y -= before
        for idx, line in enumerate(wrapped):
            ensure_space(leading)
            prefix = "* " if (kind == "bullet" and idx == 0) else ""
            x = margin_x + (0 if (kind == "bullet" and idx == 0) else indent)
            y -= leading
            pages[-1].append(
                f"BT /{font} {size} Tf {r:.2f} {g:.2f} {b:.2f} rg 1 0 0 1 {x:.1f} {y:.1f} Tm ({_pdf_escape_text(prefix + line)}) Tj ET"
            )
        y -= after

    if not any(pages):
        pages = [["BT /F1 11 Tf 0.14 0.18 0.17 rg 1 0 0 1 56 720 Tm (Pipkin Document) Tj ET"]]

    # Add subtle page numbers
    total_pages = len(pages)
    for idx, cmds in enumerate(pages, 1):
        cmds.append(
            f"BT /F1 9 Tf 0.45 0.50 0.48 rg 1 0 0 1 {page_w - margin_x - 36:.1f} 30.0 Tm (Page {idx} / {total_pages}) Tj ET"
        )

    # Assemble PDF objects:
    # 1: Catalog, 2: Pages, 3: Helvetica, 4: Helvetica-Bold, then pairs of (Page, Content)
    objs = []
    page_ids = []
    next_id = 5
    page_entries = []
    for cmds in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        stream = ("\n".join(cmds) + "\n").encode("cp1252", "replace")
        compressed = zlib.compress(stream)
        page_entries.append((page_id, content_id, compressed))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objs.append((2, f"<< /Type /Pages /Kids [ {kids} ] /Count {len(page_ids)} >>".encode("ascii")))
    objs.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"))
    objs.append((4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"))
    for page_id, content_id, compressed in page_entries:
        objs.append((
            page_id,
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii"),
        ))
        objs.append((
            content_id,
            f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode("ascii")
            + compressed + b"\nendstream",
        ))

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {0: 0}
    for obj_id, body in objs:
        offsets[obj_id] = len(buf)
        buf.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        buf.extend(body)
        buf.extend(b"\nendobj\n")

    xref_pos = len(buf)
    count = len(objs) + 1
    buf.extend(f"xref\n0 {count}\n0000000000 65535 f \n".encode("ascii"))
    for obj_id in range(1, count):
        buf.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(buf)
