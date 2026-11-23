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

- 2026-11-23: Prepared v1.2. Harness output tiles use the originating workspace and the launcher closes when the native browser opens. Undo All records completed actions and points out failed or skipped files if it stops. Check a saved output from Review and a multi-file undo.

- 2026-11-23: Fixed a boot crash from the Harness side panel: syncAgentControls() ran at startup and reached syncHarnessPanel() before `const harnessPanel` was declared (temporal dead zone), so chat.js stopped at that line: no chat history, models stuck on "Loading models…", status stuck on "Connecting", and most buttons without handlers. The Harness element lookups now sit with the other lookups at the top of chat.js. Rule: any top-level code that runs during boot may only use `const`/`let` declared above it; verify a page change by loading it in a browser and checking the console, not only with node --check.

- 2026-11-23: v1.1.3 live model residency & Mac memory-pressure HUD (`GET /api/telemetry`). Added macOS `kern.memorystatus_vm_pressure_level` + `hw.memsize` + Ollama `/api/ps` telemetry in `server.py`, turned the topbar `#model-status` pill into an interactive button showing live loaded RAM (`• Ready | 10.3 GB`) with warning/critical pressure accents and a frosted-glass popover with RAM share meter, loaded model breakdown, and quick **Unload model** / **Model settings…** actions; bumped `shell/Info.plist` to `1.1.3` (build `12`) and added `tests/test_telemetry.py`. Also includes adaptive step idle/active budgets (`04ff44d`) and built-in tool / `terminal_run` hang guards (`e6bb3ad`). Check clicking the topbar status pill when a model is idle vs loaded and unloading from the popover.

- 2026-11-22: First-launch code-burst onboarding (`dist/onboarding.js`, `/welcome`) & select-box polish. Added a cinematic first-launch intro where 18 monospace code shards accelerate into the center logo with a dual luminous shockwave, then reveal the glass onboarding card ("Welcome to Pipkin Desktop Harness — Your private local-model workspace—plan, build, and verify on your Mac with full control.") followed by optional Theme, Model (+ Ollama install guide with copyable `ollama pull qwen3:8b`), Personalization, and Ready steps (`pipkin-onboarded` flag; replay via **Settings › General › Welcome setup › Replay** or `/welcome`, click intro to skip). Upgraded all `<select>` boxes with custom `.select-wrap` glass dropdowns and expanded `html.theme-transition` to crossfade every surface smoothly over 420 ms. Check Settings › Replay (or `/welcome`), the code-burst intro, Light ↔ Dark crossfade, and select dropdowns.

- 2026-11-22: v1.1.1 Light Mode & launch choreography polish. Deepened Light Mode into a cool glacial-porcelain canvas (`#edf3f1` bg / `#e3ece8` sidebar) with crisp floating white frosted-glass cards, dual-lobe emerald-cyan aurora, and elevated tinted shadows so Light Mode matches Dark Mode's futuristic depth; added first-launch reveal (`app-boot` gliding sidebar, topbar, composer, and floating emblem), smooth Light↔Dark crossfade (`theme-transition`), Chat↔Agent welcome crossfade + `#agent-controls` slide-in, chat-switch fade, sidebar hover glide, timeline step entrances, and an animated emerald-ring native launch splash in `shell/PipkinApp.m` (version set to `1.1.1` build `11`). Check launching Pipkin, toggling Light ↔ Dark in Settings, switching Chat ↔ Agent, and switching between saved chats.

- 2026-11-22: v1.1.2 atomic whole-run undo. Added `agent.UndoStore.apply_many()` with two-phase preflight conflict detection (verifies every file's workspace identity and SHA-256 before modifying disk, returning per-file conflicts and leaving all files untouched if any changed), `POST /api/agent/undo-run` in `server.py`, and a two-click `Undo all` button in the Work result card header when a run has 2+ undoable files; bumped `shell/Info.plist` to `1.1.2` and added unit tests in `tests/test_undo.py`. Check a multi-file Agent run's `Undo all` flow both cleanly and after manually editing one of the output files.

- 2026-11-22: v1.1.1 visual redesign, centering fixes, and UI/shell optimizations. Replaced asymmetric topbar grid with a symmetric 1fr/auto/1fr grid and workspace `@container` queries; fixed the floating model-picker chevron (`#model-picker-label`); made conversation padding symmetric with a bottom fade scrim into the composer; added custom WebKit scrollbars, spring-sliding 3-way `.segmented` controls, custom range sliders, cursor-tracking spotlight sheen on starter cards, balanced composer tool pills, assistant model micro-badges, breathing busy/working luminous borders, `contain: layout style` paint optimizations, and safe `prefers-reduced-motion` handling; synced `NSWindow.appearance` and polished `PipkinBrowser.m` active tabs/error view, bumped `Info.plist` to `1.1.1`, and updated `Promote to Pipkin.command` to copy `Info.plist` and watch `PipkinBrowser.*`. Check Light/Dark/System theme sync with the native titlebar and browser pane, topbar/welcome centering, starter card spotlight hover, Settings sliders/segmented pills, and Agent working animations.

- 2026-11-22: Per-file undo. agent.UndoStore keeps one record per file per run (original text or None for a new file, SHA-256 of Pipkin's last version) under Application Support/Pipkin/undo/<port>, capped at 200 records / 20 MB. POST /api/agent/undo rechecks workspace access and the folder identity, and applies only if the file still matches; otherwise it returns a conflict and keeps the user's work. The result card shows Undo or Remove (two clicks). Check undo after an edit, after editing the file yourself, and Remove on a new export.

At the end of every task that changed files, add one line to this log: date, what changed, and what the user should check.
