# Pipkin for Windows (Preview — Local Chat)

Pipkin on Windows uses **Microsoft Edge WebView2** (built into Windows 11 and most Windows 10 PCs) wrapped in a lightweight C# desktop shell, running the exact same local Python backend (`server.py`) and web UI (`dist/`) as the macOS app.

## What You Need on Windows
1. **Windows 10 or Windows 11** (64-bit)
2. **Ollama for Windows** — install from [ollama.com](https://ollama.com/download/windows) and pull at least one model (e.g. `ollama pull qwen3:8b`).
3. **Python 3.9+** — install from [python.org](https://www.python.org/downloads/windows/) (check **"Add python.exe to PATH"** during setup).
4. **.NET 8 Desktop Runtime** — required to launch `Pipkin.exe` (or included if you install the .NET 8 SDK).

## Running a Built Zip (No Coding Required)
1. Download `Pipkin-windows-x64.zip` (from GitHub Actions or Releases) and extract the `Pipkin` folder.
2. Double-click **`Pipkin.exe`**.
3. Pipkin starts its local server silently on `http://127.0.0.1:8765`, opens the chat window, and automatically stops the server when you close the window.

## Building on a Windows PC
Install the [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0), then run in PowerShell from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File windows\build-windows.ps1
```

This outputs `windows\dist\Pipkin-windows-x64.zip`.

## Building Without a Windows PC (GitHub Actions)
Push to GitHub and run the **Build Windows Preview** workflow (`.github/workflows/build-windows.yml`) from the **Actions** tab. GitHub's free `windows-latest` runner tests `platform_os.py` on real Windows, compiles `Pipkin.exe`, and gives you a downloadable `Pipkin-windows-x64.zip` artifact.

## Current Preview Scope (v1.8.0)
- **Included:** Local Chat, live Windows RAM & pagefile pressure monitoring (`GlobalMemoryStatusEx`), Ollama auto-discovery, dark/light Windows titlebar sync, strict local-only navigation guard, and automatic server cleanup on exit.
- **Coming in v1.9.0:** Full Windows Agent parity (Windows PDF extraction, folder picker bridge, and Windows command execution guardrails).
