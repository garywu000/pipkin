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

- 2026-11-25: v2.2.1 macOS updater & single-instance fix (`updater.py`, `shell/PipkinApp.m`). macOS updates now swap `Pipkin.app/Contents` in place instead of moving `Pipkin.app` to `previous-version.app`, preserving the bundle inode so pinned Dock icons never turn into `previous-version` and cleaning up temporary backups (including legacy `previous-version.app`). Added a single-instance guard in `PipkinApp.m` (activating the existing Pipkin without unloading its model), clean multi-instance/server shutdown in `apply-update.sh`, and a blocker preventing backup copies from updating themselves. Check one-click Restart to update with Pipkin pinned in the Dock.

- 2026-11-25: v2.2.0 Scheduled & background tasks (`scheduler.py`, `dist/schedule.js`, `server.py`, `agent.py`). Local scheduler runs recurring or one-time tasks (once, daily, weekdays, weekly) while Pipkin is open with strict unattended defaults (read-only or new-files-only `create` mode, no shell commands, defers when the model is busy or RAM pressure is critical, offers to catch up or skip missed runs instead of auto-running). Finished runs land in the Scheduled inbox with Markdown summaries, copy, clickable workspace files, verified skill check badges, and cross-port Undo run. Optional skill picker connects v2.1 skills & verification checks to scheduled runs, folder picking no longer mutates your chat workspace, and `/schedule` opens Scheduled from the composer. Check sidebar › Scheduled, creating a task with or without a skill, Run now, Copy, and Undo run.

- 2026-11-25: v2.1.2 Queue and steer. `dist/chat.js`: messages sent while a reply or Work run is going either steer the run or wait in a per-chat queue above the composer (Settings › Agent › Messages during a run; Option/Alt+Return does the other one). Queued messages send one at a time after a finished reply; a stop, error, or another open chat pauses the queue with Send next, and each item can be steered in, moved up, edited, or removed. Chat titles retry while the model is busy and again after later replies. `agent.py`: lines added after a one-line snippet that matched past the file's indentation get that indentation (`indent_added_lines`). `dist/style.css`: reply headings no longer replay the welcome fade while streaming, and the closed side panel no longer makes the page scroll sideways. Also ships the welcome-card height animation, logo handoff, and animated dialog closing. Check a queued message during a Work run, Steer now, and Stop then Send next.

- 2026-11-25: v2.1.1 Agent reliability from a live Qwen 27B (IQ3_XXS) test. `agent.py`: near-repeat command guard (same output with no file change → told to move on, a third near-copy is not run, then the stall pause), `loose_edit` fallback for snippets indented differently per line, a once-per-file refusal of `write_file` rewrites that would delete existing lines, `[S1]` chat source IDs stripped from saved files, `read_file`/`read_files` described as reading PDF and Word text (plus a one-time tip when a command scripts a PDF), `read_files` takes six files and names any it skipped, and bare task filenames ("calc.py") found in subfolders no longer trigger a create-the-file nudge. `dist/chat.js`/`server.py`: a message sent after a stopped run continues it with the message as a steering note; runs the server refused show the real reason and no Resume Work; a busy model is retried for about 12 s. Check a stopped run, then type a correction; check an edit whose old_text indentation is off.

- 2026-11-25: Added an Agent model guide for Mac and Windows with unified memory, dedicated VRAM, and CPU guidance, model examples, context starting points, and benchmark coverage. Check the hardware table from either README.

- 2026-11-25: Updated the GitHub README and Windows guide for v2.1.0 with platform download buttons, first-launch steps, and shareable skills. Check the download buttons and the three-step setup on GitHub.

- 2026-11-25: Fixed Windows workspace skill discovery for v2.1.0. Directory entries on Windows do not report link counts, so the loader now checks real file metadata before reading and still rejects hard links. Check that folder skills appear in `/skills` on Windows.

- 2026-11-25: v2.1.0 Shareable Skills & Verified Workflow Packs. Added `skills.py` and upgraded `dist/skills.js` with the `.pipkin-skill` / `.pipkin-skill-pack` and Markdown frontmatter formats, 7 declarative post-run verification checks (file match, local HTML links, required heading/text, min words, citations, no TODO/TBD placeholders) with automatic one-shot self-fix retry in `agent.py` and timeline check cards in `dist/agent-view.js`, a Security Preview & Install dialog (with drag-and-drop import, export, and static safety scan for prompt-override/shell/network/secret patterns), and project-scoped Workspace Skill Packs (`.pipkin/skills/` loaded safely without symlink escapes and protected from agent writes). Check `/skills` (Import, Export, New skill with checks, Save to folder), dropping a `.pipkin-skill` file onto Pipkin, and running a workspace skill from `.pipkin/skills/`.
