<p align="center">
  <img src="dist/favicon.png" alt="Pipkin app icon" width="88" height="88">
</p>
<h1 align="center">Pipkin</h1>
<p align="center">A local AI chat and workspace agent for macOS.</p>
<p align="center">
  <a href="https://github.com/garywu000/pipkin/releases/tag/v1.0.0"><strong>Download Pipkin v1.0</strong></a> ·
  <a href="#try-site">Try /site</a> ·
  <a href="#build-from-source">Build from source</a>
</p>

Pipkin pairs a native Mac app with an Ollama model you install yourself. Ask questions in **Chat**, or use **Agent** to turn source documents into local files with a visible plan, reviewable changes, and links to open the results.

## Get started

1. Install [Ollama for macOS](https://ollama.com/download/mac) and download a model that fits your Mac. Choose a model with tool support for Agent tasks.
2. Open the [v1.0 release](https://github.com/garywu000/pipkin/releases/tag/v1.0.0), extract the Apple silicon zip, and open Pipkin. The download uses ad-hoc signing. If macOS blocks it, build a locally signed copy using the instructions below.
3. Select your installed model from the top bar. Choose Chat or Agent when starting a new session. In Agent, choose a project folder and keep **Ask first** enabled to review proposed changes.

Pipkin loads the model when you send your first request. Use **Unload model** to free its memory while keeping the app open.

## Try /site

Save [the sample brief](examples/study-session/brief.md) in a fresh folder, then choose that folder in Agent. Type **/site**, press **Enter** to select the skill, and add the following brief to the composer.

~~~text
Build a printable Study Session page in index.html using brief.md.
Include the meeting time and checklist. Make it readable on a phone and
link back to brief.md. Use HTML and CSS, verify the local link, and
report the saved page.
~~~

When prompted, review the proposed edit. Open `index.html` directly from the saved output in Pipkin.

## What v1.0 includes

| | |
| --- | --- |
| **Chat** | Saved conversations and projects, attachments, LaTeX, a thinking panel, and context compaction. |
| **Agent** | Workspace file tools, a progress timeline, edit and command approvals, steering during a run, and resumable Work checkpoints. |
| **Skills** | `/site` for local sites with link checks, `/humanize` and `/humanize-paper` for rewriting drafts, and custom recipes. |
| **macOS** | A native window, output links that open in macOS, an embedded browser and terminal, and the optional Sprig desktop pet. |

## Privacy and permissions

Use a downloaded Ollama model for on-device inference. Pipkin stores chats and app settings locally. Web searches, page fetches, and browser activity can contact external sites. The built-in file tools operate within the selected workspace. An approved shell command runs with your macOS user's permissions. See [Security](SECURITY.md) for reporting instructions.

## Build from source

The app bundle targets macOS 12 and later. The v1.0 download was built and tested on Apple silicon. Install the Xcode Command Line Tools and Ollama to build and run the app locally.

~~~sh
git clone https://github.com/garywu000/pipkin.git
cd pipkin
sh shell/package.sh
open build/Pipkin.app
~~~

The packaging script also writes a zip named for the build architecture. Run the Python regression tests with the standard library test runner.

~~~sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
~~~

## Repository layout

| Path | Role |
| --- | --- |
| [`shell/`](shell/) | Native window, browser pane, Sprig, and macOS packaging |
| [`dist/`](dist/) | Chat UI, skill menu, and Agent timeline |
| [`server.py`](server.py) | Loopback server, Ollama session, and approval handling |
| [`agent.py`](agent.py) | Agent loop, workspace tools, and site verification |
| [`tests/`](tests/) | Agent and workspace regression tests |
| [`PIPKIN.md`](PIPKIN.md) | Development notes and roadmap |

Use [GitHub Issues](https://github.com/garywu000/pipkin/issues) for bugs and ideas. Project licensing is pending. The bundled libraries have their own [license notices](THIRD_PARTY_NOTICES.md).
