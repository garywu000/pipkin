<p align="center">
  <img src="dist/favicon.png" alt="Pipkin app icon" width="88" height="88">
</p>
<h1 align="center">Pipkin</h1>
<p align="center"><strong>A local AI chat and workspace agent for the Mac you already have.</strong><br>
Turn your own files into finished work with a model that runs on your machine.</p>
<p align="center">
  <a href="https://github.com/garywu000/pipkin/releases/tag/v1.1.0"><strong>Download v1.1</strong></a> ·
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
2. Download the [v1.1 release](https://github.com/garywu000/pipkin/releases/tag/v1.1.0), unzip it, and open Pipkin. This build is Apple silicon only and ad-hoc signed. If macOS blocks it, [build it yourself](#build-from-source).
3. Pick your model in the top bar, then choose **Chat** or **Agent**. In Agent, choose a project folder and keep **Ask first** on so you review every edit and command.

Use **Unload model** in the top bar to free memory without quitting.

## Try /site

Copy [the sample brief](examples/study-session/brief.md) into an empty folder and choose that folder in Agent. Ask Pipkin to build the page in plain language.

~~~text
Build a printable Study Session page in index.html using brief.md.
Include the meeting time and checklist. Make it readable on a phone and
link back to brief.md. Use HTML and CSS, verify the local link, and
report the saved page.
~~~

Pipkin plans the work, shows the proposed `index.html` as a diff for your approval, and finishes with a link that opens the saved page.

> [!NOTE]
> Pipkin detects straightforward site requests and renders a first page from a checked content plan. Work’s editor handles interactive sites, larger projects, and updates to existing pages. You can also select `/site` from the slash menu.

## Create a document or presentation

Select an Agent workspace, then ask for the output directly.

- “Create a Word report named `meeting-notes.docx` from the attached notes.”
- “Make a five-slide PowerPoint from `brief.md`.”
- “Create a printable PDF handout from `brief.md`.”

Pipkin previews the content for approval in **Ask first**, saves the file, and shows a clickable output. `/word`, `/powerpoint`, and `/pdf` are optional shortcuts. Office exports use editable text and templates. The PowerPoint template supports one to nine slides. Check the names, numbers, and wording in the preview before approving.

## New in v1.1

- **Deliverables in plain language.** Ask for a website, Word document, PowerPoint, or PDF. Pipkin plans the content with the model, then renders and checks the file itself.
- **Work result card with undo.** Each run ends with the files it created or edited, the checks that actually ran on the current version, and the source pages it used. **Undo** restores a file's original and **Remove** deletes a file Pipkin created, but only while the file still matches what Pipkin saved, so your later edits are never overwritten.
- **Plan first.** The model writes its own checklist before acting. It stays pinned above the timeline while thinking and actions appear below.
- **Recovery.** Error-specific hints, a change of approach after a repeated failure, one recovery step before pausing, and commands that never exit are refused or stopped.
- **Chat titles** written by the model after the first reply.
- **Humanize** follows pasted instructions, has an essay mode for personal statements, and enforces word limits using an exact count that matches Google Docs.

## What Pipkin includes

| | |
| --- | --- |
| **Chat** | Saved chats and projects, model-written titles, file and image attachments, LaTeX, a thinking panel, optional web search, and context compaction |
| **Agent** | Workspace file tools, a model-written plan, a progress timeline, edit and command approvals, steering mid-run, stop after this step, resumable checkpoints, and a result card with per-file undo |
| **Deliverables** | Sites, Word documents, PowerPoint decks, and PDFs from plain-language requests, with link, archive, and page checks |
| **Long runs** | Prompt-cache-friendly context, a guard that catches looping reasoning, recovery hints for failed actions, a six-minute active limit that excludes approval time, and a two-minute model-step limit |
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

### v1.1: reliable deliverables (released)

- **Staged file creation** for sites, Word, PowerPoint, and PDF, with rendering and file checks handled by Pipkin.
- **A Work benchmark.** [Six fixture tasks](benchmarks/README.md) graded on the files they leave on disk, with time to first verified output, total time, and memory use.
- **Result cards you can trust and undo**, plus plan-first runs and recovery from stuck actions.

### v1.2: ready for everyone

- **Signed and notarized builds**, a clean-Mac install guide, and a chosen license.
- **Live benchmark comparisons** with matched model, context, and Mac settings, published with the results.
- **Undo for whole runs**, building on per-file undo.

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

The app targets macOS 12 or later. The v1.1 download was built and tested on Apple silicon. You need the Xcode Command Line Tools and Ollama.

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
| [`agent.py`](agent.py) | Agent loop, workspace tools, context management, recovery, undo, and site checks |
| [`artifacts.py`](artifacts.py) | Content plans and rendering for sites, Word, PowerPoint, and PDF |
| [`benchmarks/`](benchmarks/) | Work benchmark tasks, graders, and recorded baselines |
| [`tests/`](tests/) | Agent and workspace regression tests |
| [`docs/screenshots/`](docs/screenshots/) | Images used in this README |

## License

Project licensing is still being decided. Bundled libraries have their own [license notices](THIRD_PARTY_NOTICES.md).
