<p align="center">
  <img src="dist/favicon.png" alt="Pipkin app icon" width="88" height="88">
</p>
<h1 align="center">Pipkin</h1>
<p align="center"><strong>A local AI chat and workspace agent for the Mac you already have.</strong><br>
Turn your own files into finished work with a model that runs on your machine.</p>
<p align="center">
  <a href="https://github.com/garywu000/pipkin/releases/tag/v1.0.0"><strong>Download v1.0</strong></a> ·
  <a href="#get-started">Get started</a> ·
  <a href="#try-site">Try /site</a> ·
  <a href="#roadmap">Roadmap</a> ·
  <a href="#build-from-source">Build from source</a>
</p>

<p align="center">
  <img src="docs/screenshots/agent-approval.png" alt="Pipkin's Agent building a page from brief.md: a three-step plan, a thought summary, and an approval card showing the proposed index.html" width="900">
</p>

Pipkin is a native macOS app for an [Ollama](https://ollama.com) model you install yourself. **Chat** answers questions privately. **Agent** works inside a folder you choose: it makes a plan, reads your files, proposes edits you approve, and links to what it saved.

The goal is narrow on purpose: help a mid-size local model, on an ordinary Mac, **actually finish** real tasks within your time and memory budget.

## Highlights

- **Private by default.** Inference runs on your Mac through Ollama. Chats and settings stay local. The model loads on your first message and unloads when you quit.
- **An agent you can watch.** The run shows its plan, its reasoning, and every action. Edits appear as diffs and wait for your approval in **Ask first** mode. You can send steering notes mid-run, stop after the current step, or resume later from a saved checkpoint.
- **Built for slow local models.** Pipkin keeps the prompt cache-friendly, catches reasoning that loops, and keeps the model loaded while it waits for your approval.
- **Skills.** `/site` turns a brief into a local web page and checks its links. `/humanize` and `/humanize-paper` rewrite drafts. You can also save your own recipes.
- **A real Mac app.** Native window, a built-in browser with tabs and a terminal (both available to the agent when you allow it), links that open in macOS, and Sprig, an optional desktop pet that tells you when a run needs you.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/chat.png" alt="A chat answer from qwen3:8b explaining heat pumps, with rendered math, a thinking summary and token speed"></td>
    <td width="50%"><img src="docs/screenshots/welcome.png" alt="Pipkin's start screen with suggested prompts, the model picker and the Chat or Agent switch"></td>
  </tr>
  <tr>
    <td align="center"><sub>Chat: rendered math, a thinking summary, and tokens per second</sub></td>
    <td align="center"><sub>Choose a model, then Chat or Agent</sub></td>
  </tr>
</table>

## Get started

1. Install [Ollama for macOS](https://ollama.com/download/mac) and pull a model that fits your Mac. Agent needs a model with tool support. `qwen3:8b` is a good start on 16 GB of memory:
   ~~~sh
   ollama pull qwen3:8b
   ~~~
2. Download the [v1.0 release](https://github.com/garywu000/pipkin/releases/tag/v1.0.0), unzip it, and open Pipkin. This build is Apple silicon only and ad-hoc signed. If macOS blocks it, [build it yourself](#build-from-source).
3. Pick your model in the top bar, then choose **Chat** or **Agent**. In Agent, choose a project folder and keep **Ask first** on so you review every edit and command.

Use **Unload model** in the top bar to free memory without quitting.

## Try /site

Copy [the sample brief](examples/study-session/brief.md) into an empty folder and choose that folder in Agent. Type **/site**, press **Enter** to pick the skill, and send this:

~~~text
Build a printable Study Session page in index.html using brief.md.
Include the meeting time and checklist. Make it readable on a phone and
link back to brief.md. Use HTML and CSS, verify the local link, and
report the saved page.
~~~

Pipkin plans the work, shows the proposed `index.html` as a diff for your approval, and finishes with a link that opens the saved page.

> [!NOTE]
> Small models don't always finish multi-step tasks. If a run stops early, send a steering note such as "Continue and verify the page", or use a larger model. Making `/site` reliable on 8B models is the top item on the roadmap.

## What v1.0 includes

| | |
| --- | --- |
| **Chat** | Saved chats and projects, file and image attachments, LaTeX, a thinking panel, optional web search, and context compaction |
| **Agent** | Workspace file tools, plan and progress timeline, edit and command approvals, steering mid-run, stop after this step, resumable checkpoints |
| **Long runs** | Prompt-cache-friendly context, a guard that catches looping reasoning, a 90-minute working-time limit that excludes your approval time, and nudges to start writing |
| **Skills** | `/site`, `/humanize`, `/humanize-paper`, and custom recipes |
| **macOS** | Native window, built-in browser (tabs, Google search) and terminal, notifications when approval is needed, and the optional Sprig desktop pet |

## Privacy and permissions

- Model inference runs locally through Ollama. Pipkin never sends your chats to a hosted model.
- Web search, page fetches, and the built-in browser contact the sites involved. Turn web access off per chat or per run.
- The agent's file tools are limited to the folder you choose. The model can't reach anything outside it on its own.
- **Shell commands are different.** An approved command runs with your macOS user's permissions. Keep **Ask first** on unless you trust the task.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Roadmap

Pipkin's bet: the most useful local agent is the one that **finishes**. Each release is judged on how often a mid-size model completes a real task on an everyday Mac, how long that takes, and whether the Mac stays responsive.

### v1.1: reliable deliverables

- **A staged `/site` runner.** Code handles file discovery, rendering and link checks, and the model writes the content plan and the design. This fixes the two biggest 8B failure modes: exploring forever without writing, and stopping before verifying.
- **A public benchmark.** Six fixture tasks: PDF to site, updating an existing page, a small code fix, and stop-and-resume. Each is graded on the files it leaves on disk. Track completion rate, time to first verified output, total time, and memory pressure for Pipkin and one other harness, using the same model and Mac. Results ship in the repo.
- **Result cards you can trust and undo.** Every run ends with its outputs, the source pages it used, the checks it actually ran, and a diff. One click undoes Pipkin's edits, after checking you haven't changed those files since.
- **Signed and notarized builds**, a clean-Mac install guide, and a chosen license.

### v2: long documents and long runs

- **Document-first workflows.** Drop in a PDF packet, syllabus, or brief and get a navigable site, a study guide, or a checklist, with every claim linked to its page.
- **Working memory that survives trimming.** The agent keeps short notes of what it has learned, so it stops re-reading files it already read.
- **Memory-pressure awareness.** Pipkin shows when the Mac is under pressure and pauses or unloads the model before the system starts swapping.
- **Two-model runs.** A small model explores and a larger one writes, but only if the benchmark shows it finishes more tasks.
- **Safer shell access.** Commands become a separate opt-in, run in an isolated environment, and come with a clear list of what they may touch.

### v3: connected and extensible

- **Read-first connectors** for GitHub, Google Drive, and Calendar: summarize, plan, and draft locally. Writing back always needs approval.
- **Shareable skills.** A small file format for workflows like `/site`, with their own checks, so people can publish and install them.
- **Scheduled and background tasks**, such as "every Friday, turn this week's notes into a review sheet", with a summary waiting for you.
- **Pick up where you left off** on another Mac, without a cloud account.

Have an idea or a task Pipkin should finish? [Open an issue](https://github.com/garywu000/pipkin/issues).

## Build from source

The app targets macOS 12 or later. The v1.0 download was built and tested on Apple silicon. You need the Xcode Command Line Tools and Ollama.

~~~sh
git clone https://github.com/garywu000/pipkin.git
cd pipkin
sh shell/package.sh
open build/Pipkin.app
~~~

The packaging script also writes a zip named for your build architecture. To run the regression tests:

~~~sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
~~~

### Repository layout

| Path | Role |
| --- | --- |
| [`shell/`](shell/) | Native window, browser pane, Sprig, and macOS packaging |
| [`dist/`](dist/) | Chat UI, skill menu, terminal panel, and Agent timeline |
| [`server.py`](server.py) | Loopback server, Ollama session, terminal, and approvals |
| [`agent.py`](agent.py) | Agent loop, workspace tools, context management, and site checks |
| [`tests/`](tests/) | Agent and workspace regression tests |
| [`docs/screenshots/`](docs/screenshots/) | Images used in this README |

## License

Project licensing is still being decided. Bundled libraries have their own [license notices](THIRD_PARTY_NOTICES.md).
