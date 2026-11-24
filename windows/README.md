# Pipkin for Windows (Local Chat & Workspace Agent)

Pipkin on Windows uses **Microsoft Edge WebView2** (built into Windows 11 and most Windows 10 PCs) wrapped in a lightweight C# desktop shell, running the same local Python backend (`server.py`, `agent.py`, `artifacts.py`) and web UI (`dist/`) as the macOS app.

## What You Need on Windows
1. **Windows 10 or Windows 11** (64-bit)
2. **Ollama for Windows** — install from [ollama.com](https://ollama.com/download/windows) and pull at least one model (e.g. `ollama pull qwen3:8b`).
3. **Python 3.9+** — install from [python.org](https://www.python.org/downloads/windows/) (check **"Add python.exe to PATH"** during setup).
4. **.NET 8 Desktop Runtime** — required to launch `Pipkin.exe` (or included if you install the .NET 8 SDK).

## Running a Built Zip (No Coding Required)
1. Download `Pipkin-windows-x64.zip` (from GitHub Actions or Releases) and extract the `Pipkin` folder.
2. Double-click **`Pipkin.exe`**.
3. Pipkin starts its local server silently on `http://127.0.0.1:8765`, opens the app window, and cleanly stops the server when you close it.

## Building on a Windows PC
Install the [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0), then run in PowerShell from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File windows\build-windows.ps1
```

This outputs `windows\dist\Pipkin-windows-x64.zip`.

## Building & Testing Without a Windows PC (GitHub Actions)
Push to GitHub and run the **Build Windows Package** workflow (`.github/workflows/build-windows.yml`) from the **Actions** tab. GitHub's `windows-latest` runner executes the full Python unit test suite and agent benchmarks directly on real Windows, compiles `Pipkin.exe`, and uploads a ready-to-run `Pipkin-windows-x64.zip` artifact.

## Included in v1.9.0 (Full Windows Parity)
- **Local Chat & Workspace Agent:** Read, search, edit, diff, undo, and checkpoint files safely inside a chosen Windows folder (blocks symlinks, NTFS junctions, drive escapes, and reserved device names).
- **Native Windows Folder Picker:** Opens Windows' native folder chooser right in front of the Pipkin window.
- **Zero-Dependency PDF Support:** Read PDFs (with page citations) and generate `/pdf`, `/study-guide`, `/checklist`, `/word`, and `/slides` deliverables on Windows with bare Python (and automatically uses `pypdf`/`PyMuPDF` if installed).
- **Shell & Terminal Parity:** Opt-in isolated PowerShell command runner with approval cards and sanitized environment, plus built-in PowerShell terminal panel.
- **Native Windows UI:** Replaces macOS wording and `⌘`/`⌥`/`⇧` symbols with Windows `Ctrl`/`Alt`/`Shift` shortcuts, Windows copy, Dark/Light titlebar sync, and taskbar attention flashing.
