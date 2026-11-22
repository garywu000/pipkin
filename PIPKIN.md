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

- 2026-11-22: Per-file undo. agent.UndoStore keeps one record per file per run (original text or None for a new file, SHA-256 of Pipkin's last version) under Application Support/Pipkin/undo/<port>, capped at 200 records / 20 MB. POST /api/agent/undo rechecks workspace access and the folder identity, and applies only if the file still matches; otherwise it returns a conflict and keeps the user's work. The result card shows Undo or Remove (two clicks). Check undo after an edit, after editing the file yourself, and Remove on a new export.

- 2026-11-22: Clipped chat titles fade at the edge. Settings › Usage shows lifetime input and generated tokens from Ollama's final response counts. The local counter is independent of chat history. Check the Usage total before and after deleting a chat.

- 2026-11-22: The chat sidebar remembers its width and has a drag handle with keyboard controls. Long sidebar and header titles scroll on hover or focus. Check a narrow sidebar, a long title, and the saved width after reopening.

- 2026-11-22: Word counts come from code, never the model. count_words()/countWords() follow Google Docs rules (spaces and em/en dashes split; hyphenated words count once). Humanize reads the limit from the instructions (word_limit(), 350 for a UC PIQ), gives the model the exact draft count and target, and retries up to twice with "Pipkin counted N words; remove at least K"; the note shows "draft → result words · limit". Chat and Agent messages that ask about words get Pipkin's exact count attached (wordCountNote). Check a PIQ with "under 350 words".

- 2026-11-22: Pastes over 10,000 characters become text attachments with a full preview. Pipkin saves the original in chat history and sends an excerpt using the existing attachment budget. Check a long paste, Move to composer, and reopening a saved chat.

- 2026-11-22: Humanize follows pasted instructions. splitHumanizeRequest() separates a leading "rewrite this…:" line and a "Prompt:" line from the draft, infers tone (direct/warm/professional) unless --tone is given, and picks an essay mode (ESSAY_EDITOR, no short-message examples) for essays/PIQs. The server retries once when the rewrite is unchanged and returns unchanged: true, shown as a note instead of a silent copy. Check a UC PIQ pasted with its instructions and prompt above it.

- 2026-11-22: Model-written chat titles. firstTurnTitle() stays as the instant placeholder; after the first exchange is saved, refineChatTitle() asks /api/title (only if the model is already loaded and idle; JSON schema, 40 tokens, clean_title()) and saveModelTitle() stores it as titleSource "model" without switching chats. Manual renames win. Nothing appears in the timeline; the next turn waits for an in-flight title (20 s cap). When it arrives, animateRetitle() fades the old title and types the new one into the sidebar row and header (skipped with Reduce Motion). Check a new Chat and Agent chat get a short summary title in the sidebar a few seconds after the first reply.

- 2026-11-22: Recovery and stalls. Fixed a hidden approval: the agent view only redrew on requestAnimationFrame, which WebKit pauses for a background window, so a Word export sat on "Running…" while the server waited for approval; paint() now falls back to a 300 ms timer and the shell sets inactiveSchedulingPolicy = none (macOS 14+). Unexpected server errors end the run with a message and a traceback in server.log. Failed actions get an error-specific (Recovery: …) hint, a repeated failure asks for a new approach, nonzero command exits no longer count as progress, and two stalled steps trigger one recovery prompt before the pause at four. Check an export with Pipkin in the background, and a run that hits a missing file.

At the end of every task that changed files, add one line to this log: date, what changed, and what the user should check.
