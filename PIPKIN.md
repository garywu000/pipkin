# Pipkin dev copy: standing instructions

You are improving Pipkin, the app you are running inside. This folder is a separate dev copy, so edits here cannot break the running app. The user previews it with Pipkin's Develop › Open Pipkin Dev Window (port 8766) and promotes good changes with Develop › Promote Dev to Pipkin.

## Map (use outline_file, then read_file only the lines you need)
- `server.py`: Python HTTP server on 127.0.0.1. Serves `dist/`, relays chat to Ollama (`relay_chat`), runs the agent (`run_workspace_agent`, `agent_step`), and handles models (`list_models`, `select_model`), approvals (`approve_action`) and folders (`choose_workspace`, `restore_workspace`).
- `agent.py`: agent tools and loop. `Workspace` (safe file access), `web_search`, `web_fetch`, `run_command`, tool schemas (`READ_TOOLS`, `WRITE_TOOLS`), `system_prompt`, and `class Agent` (`fit` trims context, `execute` runs tools, `run` is the loop).
- `dist/index.html`: markup, SVG icon sprite (`<symbol id="i-name">`), dialogs, settings panels.
- `dist/style.css`: design tokens in `:root`, with dark mode in two places (the media query and `[data-theme="dark"]`). Sections are marked `/* ---------- Name ---------- */`.
- `dist/ui.js`: `PipkinUI` with `icon()`, `el()`, `toast()`, `copyText()`, and the Markdown renderer `renderMarkdown()`.
- `dist/skills.js`: Built-in recipes, slash parsing, custom-skill validation, and steps extracted from completed Work runs.
- `dist/agent-view.js`: `PipkinAgent`, the agent timeline. `apply()` handles stream events; `mount()` renders them.
- `dist/chat.js`: everything else. Storage, sidebar, messages (`addMessage`), sending (`submitTurn`), models, settings and shortcuts.
- `dist/vendor/katex.min.js`: third-party. Never edit.
- `shell/PipkinApp.m`: the native macOS app (WebKit window, menus, starts/stops the server, unloads the model on quit). Build with `sh shell/build.sh`. The page talks to it through `notifyShell()` and `window.PipkinShell` in chat.js.
- `shell/PipkinPet.m`: Sprig's right-edge panel, hover animation, and native quick actions.

## Rules
- Make one small, focused change per task. Read the exact lines before editing, and edit with a unique `old_text`.
- Build DOM with `el()` and `textContent`. Never use `innerHTML` with model or user text.
- CSP only allows same-origin scripts: no CDNs and no inline `<script>`.
- Every new color needs a token in `:root` plus both dark-mode blocks.
- Never weaken safety checks in `agent.py` (path validation, private-network blocks, approvals).
- Do not rename stream events or storage keys without updating both sides (server/agent ↔ agent-view/chat).

## Verify every change (run_command)
- JS: `node --check dist/chat.js` (and any other JS file you changed).
- Python: `python3 -c "import ast,sys;[ast.parse(open(f).read()) for f in sys.argv[1:]]" server.py agent.py`
- Agent regressions: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`
- Then commit: `git add -A && git commit -m "<short summary>"`. The user can undo with git.
- Prefer search_text and outline_file over shell grep; they are faster and cheaper.
- You cannot see the UI. Tell the user exactly what to check in the Pipkin Dev window (View › Reload for dist changes; Develop › Restart Dev Server for .py changes).

## Releasing (one-click updates depend on this)
1. Bump `APP_VERSION` in `server.py`, `shell/Info.plist` (version and build), and `windows/PipkinWindows.csproj`; `tests/test_update_check.py` checks they match.
2. Push `main`, then tag `vX.Y.Z` and push the tag. CI builds `Pipkin-windows-x64.zip`; build the Mac zip with `sh shell/package.sh <empty folder>`.
3. Sign both zips on the maintainer Mac: `python3 updater.py sign X.Y.Z <Pipkin-macos-arm64.zip> <Pipkin-windows-x64.zip>`. This writes `pipkin-update.json`; the private key lives in the login Keychain (`pipkin-release-signing`). Never commit it or put it in CI.
4. Upload all three files to the GitHub release. A release without `pipkin-update.json` can only be installed by hand.
5. If the Keychain key is lost, older apps can no longer update in one click: ship the new key in a release users download by hand.

## Backlog (ideas, highest value first)
- Connected accounts — Build the GitHub and Google Workspace plan below in phases.
- Agent performance — Benchmark the current planning and batching defaults on longer workloads, then assess an 8B exploration mode. Improve recovery from repeated tool failures.

## Pipkin Agent Harness release plan

1. Make the Apple Silicon preview easy to start from a clean Mac. Let the user select an installed Ollama model and workspace. Document setup, supported model capabilities, typical RAM and context settings, and how to stop or unload a run. Keep the app download small by having users install model weights separately.
2. Add a repeatable benchmark before publishing speed claims. Run a fictional PDF-to-site task, a small repo edit with verification, and a stop-and-resume task on at least two model sizes. Record task success, wall time, model steps, prompt processing, generation, approval waits, and peak model residency. Publish the model, quantization, hardware, and context alongside the results. [Ollama timing fields](https://docs.ollama.com/api/usage)
3. Audit the public release. Verify workspace and local-network boundaries, anchored approvals for edits and commands, prompt injection handling for files and web pages, and consent for access to signed-in browser tabs or an existing terminal. Exercise stop, crash recovery, and sleep assertion cleanup. Share synthetic fixtures and timing summaries, and review the full Git history and release archive for private paths, chats, logs, credentials, and project data.
4. Choose the project license and check redistribution rights for the icon, bundled browser/terminal assets, fonts, and other dependencies. Record third-party licenses and their upstream versions. Add installation, architecture, contribution, and private security-reporting instructions. [GitHub licensing guide](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)
5. Publish a tagged preview with reproducible build steps, a signed and notarized Mac download, and a small example workspace after a clean-machine install test. [Apple notarization guide](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) · [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)

The next harness improvement to benchmark is a bounded PDF-to-site workflow that keeps brief excerpts and file locations together, builds a first version early, and runs a repeatable local verification pass. After that, test whether a smaller exploration model helps enough to justify the memory and model-switch cost on supported Macs.

## Planned connections

1. Add Connected Accounts and project-level resource selection. Start GitHub reads with fixed GitHub CLI operations against selected repositories, then add a GitHub App connection with per-repository permissions. Use Google's desktop OAuth flow for sign-in and Keychain for stored credentials. [GitHub App permissions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app) · [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
2. Verify read access first. GitHub can fetch issues, PR diffs, and checks. Google Docs can use `drive.file` for documents selected through Google Picker. Add inbox-reading permissions separately if a workflow needs email threads. [Google Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
3. Add typed, individually approved writes. Start with creating and updating issues, opening draft PRs from an existing branch, posting comments, editing a selected Doc against its current revision, and sending a reviewed draft through Gmail's `gmail.send` scope. Bind approval to the exact destination and payload. Record the provider's result in Work and check an ambiguous write before retrying it. [GitHub issues](https://docs.github.com/en/rest/issues/issues) · [GitHub pull requests](https://docs.github.com/en/rest/pulls/pulls) · [Docs edits](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate) · [Gmail send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
4. Add connection-backed recipes after the adapters pass fixture and test-account checks. A skill can prepare a change using the selected account permissions. The Work approval card presents each external write for review.

## Progress log (newest first; keep at most 8 entries, delete the oldest)

- 2026-11-24: v2.0.6, the first release shipped through one-click updates (the update helper now removes its emptied staging folder). Deliberately not promoted into Pipkin.app: the user is testing Update now from v2.0.5. Check that Settings › General shows v2.0.6 after Restart to update.

- 2026-11-24: v2.0.5 one-click updates. `updater.py` verifies a signed `pipkin-update.json` (pure-Python Ed25519, key in the maintainer's Keychain), downloads and hash-checks the platform zip, extracts it safely, and stages a helper; the Mac/Windows shells run that fixed-path helper on `apply-update`, quit, and the helper swaps the app (with rollback) and reopens it. Banner: Update now → progress → Restart to update. Also a hand cursor on every clickable control. Check Settings › General and, after the next release, Update now on a v2.0.5 install.

- 2026-11-24: v2.0.4 redesign. New token system in `dist/style.css` (warm paper neutrals tinted by the palette via `--tint`, palettes only set accent families), floating main sheet framed by the window chrome, rounded display font, suggestion-card icons, pill mode switch, View Transition theme cross-fade (`applyTheme` in `dist/chat.js`), `solidColor()` so the native title bar gets rgb() even from color-mix colors, MathML sizing, and fixes for the stale scroll-to-bottom button, omnibox double outline, and see-through narrow side panel. README screenshots re-captured in WebKit. Check Light/Dark/System with each palette, the title bar color, Settings, onboarding, and a narrow window.

- 2026-11-24: v2.0.3 pre-v2.1 hygiene: connected pipkin-dev directly to GitHub origin/main (matching garywu000 commit identity), added privacy-safe GitHub Releases update check (`GET /api/update`, `POST /api/update/preference`) with dismissible update banner + Settings › General toggle & Check now button, and re-ran live `benchmarks/sim.py` checks. Check Settings › General › Check for updates and `git status -sb`.

- 2026-11-24: v2.0.2 (pushed and released). Ran live qwen3:8b Work tasks and fixed what broke: PDFs readable by file tools, no blind overwrites (write_page was wiping existing pages), nudges for narrated next steps and missing deliverables, correction when a reply claims changes that did not happen, indentation-tolerant edits, rewrite cap, no pytest/pycache clutter. Run `python3 benchmarks/sim.py qwen3:8b build/sim` to replay. Check a Work run that updates an existing page and one that reads a PDF in the folder.

- 2026-11-24: v2.0.1 security hardening (pushed and released with v2.0.0). Web fetches pin the checked DNS answer (no rebinding to localhost/LAN), the Mac command sandbox blocks open/osascript/launchctl/Apple Events, backup imports keep local shell/approval/web settings and never overwrite projects, writes re-check hard links after opening, settings/transfers save atomically as 0600, backups use 600k PBKDF2 and 8+ char passphrases, long streaming replies repaint less often. Check importing an old backup, a web fetch, and an approved command in Work.

- 2026-11-24: Reviewed v1.2 through v1.9 and updated installation, features, and version history for macOS and Windows. Verified the Windows release ZIP against the successful CI package. Included platform_os.py in Mac source builds and refreshed the memory graph screenshot.

- 2026-11-23: Updated the v1.2 README and captured new screenshots of first-launch customization, the welcome screen, Chat, Agent approval, the Harness launcher, and model memory using sample data. Check the screenshots and first-run instructions on GitHub.

At the end of every task that changed files, add one line to this log: date, what changed, and what the user should check.
