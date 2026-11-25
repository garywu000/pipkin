# Security

Report a suspected vulnerability through [GitHub private vulnerability reporting](https://github.com/garywu000/pipkin/security/advisories/new). Use a synthetic workspace in reproductions and redact paths, credentials, and document contents.

## Workspace & Command Isolation
- **Workspace file tools** validate every path inside the user-selected folder, blocking hidden secrets (`.env`, private keys, keychains), symbolic links, hard links, Windows NTFS junctions, drive/UNC escapes, and reserved device names.
- **Shell commands are off by default.** When enabled in **Settings → Agent → Allow shell commands**, every command requires explicit user approval and previews its scope:
  - **macOS:** Runs inside a macOS Seatbelt (`/usr/bin/sandbox-exec`) profile that blocks outbound/inbound network access and restricts writes to the selected workspace and a scoped temp directory, with a sanitized environment. It also blocks launching or scripting other apps (`open`, `osascript`, Apple Events, LaunchServices), which would otherwise run outside the sandbox.
  - **Windows:** Runs in PowerShell with the working directory set to the workspace and a sanitized environment (ambient API/GitHub tokens stripped, user/temp dirs redirected). Approved commands execute with the Windows user's file and network permissions — always review commands before approving.

## Cross-Device Backups & Handoff
- Optional backup encryption uses client-side **PBKDF2-SHA256** (600,000 iterations for new backups) + **AES-256-GCM** via the Web Crypto API. No keys or data are ever sent to a remote server.
- Folder handoff only reads/writes one fixed file (`pipkin-latest.pipkin-backup`) inside a folder explicitly chosen through the native OS folder picker, rejecting symbolic links and reparse points.

## Updates
- Pipkin checks GitHub Releases for a newer version when it opens (off switch in **Settings → General**). The request carries no chats, files, or settings.
- One-click updates install only a release whose `pipkin-update.json` carries a valid **Ed25519** signature from a key kept on the maintainer's Mac, never on GitHub or in CI. The download's size and **SHA-256** must match the signed manifest, the version must be newer than the running one, and the archive is rejected if it contains absolute paths, `..`, or symbolic links. A compromised GitHub account or download host cannot push code through the updater.
- The swap runs from a helper at a fixed path in Pipkin's private data folder; the page can ask the app to run it but cannot choose what runs. If the swap fails, the previous version is restored.

## Local Inference & Web Tools
- Local model inference, chat history, settings, and token totals stay on your device.
- Web search and page fetches only connect to public internet addresses: the address is checked and pinned in the same DNS lookup, so a site cannot rebind its name to `localhost` or your local network. Imported backups never change this device's shell, approval, web, or memory-protection settings.
- Web search, page fetches, and the native macOS browser pane contact external sites directly when enabled. Check the active workspace and permission mode before asking Agent to work with private files.
