# Pipkin for Windows (Local Chat & Workspace Agent)

Pipkin on Windows uses **Microsoft Edge WebView2** in a C# desktop shell and shares its local Python backend and app interface with macOS.

## What You Need on Windows
1. **Windows 10 22H2 or Windows 11** (x64)
2. **Ollama for Windows** — install from [ollama.com](https://ollama.com/download/windows) and pull at least one model (e.g. `ollama pull qwen3:8b`).
3. **Python 3.9+** — install from [python.org](https://www.python.org/downloads/windows/) (check **"Add python.exe to PATH"** during setup).
4. **[WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)** if it is missing (pre-installed on Windows 11).

*(Starting in v2.0.0, `Pipkin-windows-x64.zip` is **self-contained** — you do **not** need to install the .NET 8 Desktop Runtime separately.)*

## Running a Built Zip (No Coding Required)
1. Download [Pipkin-windows-x64.zip](https://github.com/garywu000/pipkin/releases/download/v2.0.0/Pipkin-windows-x64.zip) and extract the full `Pipkin` folder.
2. Double-click **`Pipkin.exe`**.
3. Pipkin starts its local server, opens the app window, and stops the server it started when you close it. First-launch setup lets you choose an appearance, model, and default mode.

## Building on a Windows PC
Install the [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0), then run in PowerShell from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File windows\build-windows.ps1
```

This outputs `windows\dist\Pipkin-windows-x64.zip`.

## Building & Testing Without a Windows PC (GitHub Actions)
Run **Build Windows Package** from the **Actions** tab. The Windows runner checks the source, runs the unit tests and scripted Agent benchmarks, then uploads `Pipkin-windows-x64.zip`. The [successful v1.9.0 build](https://github.com/garywu000/pipkin/actions/runs/45325460976) includes the Windows checkpoint, SQLite, and PowerShell fixes.

## Included in v1.9.0
- **Local Chat & Workspace Agent:** Read, search, edit, diff, undo, and checkpoint files safely inside a chosen Windows folder (blocks symlinks, NTFS junctions, drive escapes, and reserved device names).
- **Native Windows Folder Picker:** Opens Windows' native folder chooser right in front of the Pipkin window.
- **Documents and PDFs.** Extract text from supported PDFs with page citations and generate PDFs, HTML study guides and checklists, Word documents, and PowerPoint decks. Pipkin can use optional PDF libraries if they are installed.
- **PowerShell.** The app has a built-in PowerShell terminal. Agent commands are opt-in and require approval. Workspace commands start in the workspace with a reduced environment and use your Windows account’s file and network permissions.
- **Native Windows UI:** Replaces macOS wording and `⌘`/`⌥`/`⇧` symbols with Windows `Ctrl`/`Alt`/`Shift` shortcuts, Windows copy, Dark/Light titlebar sync, and taskbar attention flashing.

Pipkin opens web links in your default Windows browser. The integrated tabbed Browser pane and Sprig are Mac features.
