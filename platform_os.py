"""Cross-platform OS adapter for Pipkin (macOS + Windows + Linux fallback).

Keeps OS-specific paths, Ollama discovery, memory telemetry, sleep prevention,
and file/URL opening in one place so server.py runs cleanly on both macOS and Windows.
"""

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys


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
            subprocess.run(["explorer", f"/select,{target}"], check=False, timeout=5)
        else:
            os.startfile(str(target))  # type: ignore[attr-defined]
        return
    subprocess.run(["xdg-open", str(Path(target).parent if reveal else target)], check=True, timeout=5)


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
