/* Pipkin side panels: the built-in terminal (xterm.js on a local pty) and the native browser pane. */
"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const shell = window.webkit?.messageHandlers?.pipkin || null;
  const panel = byId("terminal-panel");
  const host = byId("terminal-host");
  const tabsBar = byId("terminal-tabs");
  const toggleButton = byId("toggle-terminal");
  const browserButton = byId("toggle-browser");
  const STORE = "pipkin-terminals";
  const terminals = [];
  let active = null;
  let fitTimer = 0;

  const store = {
    get(key, fallback) {
      try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
    },
    session(value) {
      try {
        if (value === undefined) return JSON.parse(sessionStorage.getItem(STORE)) || [];
        sessionStorage.setItem(STORE, JSON.stringify(value));
      } catch { return []; }
      return value;
    },
  };

  async function api(action, body) {
    const response = await fetch("/api/terminal/" + action, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Terminal request failed.");
    return response;
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function theme() {
    return {
      background: cssVar("--bg"), foreground: cssVar("--ink"), cursor: cssVar("--accent"),
      cursorAccent: cssVar("--bg"), selectionBackground: cssVar("--accent-soft") || "#2c5",
    };
  }

  function remember() {
    store.session(terminals.map(({ id, title, offset }) => ({ id, title, offset })));
  }

  /** Columns and rows that fit the visible host, from xterm's measured cell size. */
  function fit(term) {
    if (!term || panel.hidden) return;
    const cell = term.instance._core?._renderService?.dimensions?.css?.cell;
    if (!cell?.width || !cell?.height) return;
    const style = getComputedStyle(host);
    const width = host.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 14;
    const height = host.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
    const cols = Math.max(10, Math.floor(width / cell.width));
    const rows = Math.max(2, Math.floor(height / cell.height));
    if (cols === term.instance.cols && rows === term.instance.rows) return;
    term.instance.resize(cols, rows);
    if (term.id && !term.exited) api("resize", { id: term.id, cols, rows }).catch(() => {});
  }

  function scheduleFit() {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(() => fit(active), 60);
  }

  function renderTabs() {
    tabsBar.replaceChildren(...terminals.map((term, index) => {
      const tab = document.createElement("div");
      tab.className = "terminal-tab";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(term === active));
      if (term.exited) tab.dataset.exited = "";
      const label = document.createElement("button");
      label.type = "button";
      label.className = "terminal-tab-label";
      label.textContent = term.title || "zsh " + (index + 1);
      label.title = term.exited ? "Exited" : term.cwd || "";
      label.addEventListener("click", () => select(term));
      const close = document.createElement("button");
      close.type = "button";
      close.className = "terminal-tab-close";
      close.setAttribute("aria-label", "Close terminal");
      close.innerHTML = '<svg class="icon"><use href="#i-x"/></svg>';
      close.addEventListener("click", () => closeTerminal(term));
      tab.append(label, close);
      return tab;
    }));
  }

  function select(term) {
    active = term;
    for (const item of terminals) item.element.hidden = item !== term;
    renderTabs();
    if (term) {
      requestAnimationFrame(() => {
        fit(term);
        term.instance.focus();
      });
    }
  }

  async function stream(term) {
    while (!term.closed && !term.exited) {
      try {
        const response = await api("read", { id: term.id, offset: term.offset });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done || term.closed) break;
          buffer += decoder.decode(value, { stream: true });
          let newline;
          while ((newline = buffer.indexOf("\n")) >= 0) {
            const line = buffer.slice(0, newline).trim();
            buffer = buffer.slice(newline + 1);
            if (!line) continue;
            const message = JSON.parse(line);
            if (message.data) {
              const bytes = Uint8Array.from(atob(message.data), (char) => char.charCodeAt(0));
              term.instance.write(bytes);
              term.offset = message.offset;
            }
            if (message.exit !== undefined) {
              term.exited = true;
              term.instance.write(`\r\n\x1b[2m[Process exited with code ${message.exit}. Close this tab or open a new one.]\x1b[0m\r\n`);
              renderTabs();
            }
          }
        }
        remember();
      } catch (error) {
        if (/closed/i.test(error.message)) {
          term.exited = true;
          term.instance.write("\r\n\x1b[2m[This terminal is no longer running.]\x1b[0m\r\n");
          renderTabs();
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    }
  }

  function createView(saved = {}) {
    const element = document.createElement("div");
    element.className = "terminal-view";
    host.append(element);
    const instance = new window.Terminal({
      fontFamily: '"SF Mono", ui-monospace, Menlo, monospace', fontSize: 12.5, lineHeight: 1.15,
      cursorBlink: true, scrollback: 5000, allowProposedApi: false, macOptionIsMeta: true,
      theme: theme(), allowTransparency: false,
    });
    instance.open(element);
    try {
      if (window.WebglAddon) instance.loadAddon(new window.WebglAddon.WebglAddon());
    } catch { /* falls back to the DOM renderer */ }
    const term = { id: saved.id || null, title: saved.title || "", offset: 0, instance, element, exited: false, closed: false };
    const pending = [];
    let flushing = false;
    instance.onData((data) => {
      if (!term.id || term.exited) return;
      pending.push(data);
      if (flushing) return;
      flushing = true;
      queueMicrotask(async () => {
        while (pending.length) {
          const chunk = pending.splice(0).join("");
          await api("write", { id: term.id, data: chunk }).catch(() => {});
        }
        flushing = false;
      });
    });
    instance.onTitleChange((title) => {
      term.title = title.replace(/^.*@[^:]+:\s*/, "").slice(0, 40);
      renderTabs();
      remember();
    });
    instance.attachCustomKeyEventHandler((event) => {
      // Let the app's own shortcuts (⌘K, ⌘N, ⌘W …) through; the shell gets everything else.
      if (event.metaKey && event.key !== "c" && event.key !== "v") return false;
      if (event.metaKey && event.key === "c" && !instance.hasSelection()) return false;
      return true;
    });
    terminals.push(term);
    return term;
  }

  async function newTerminal() {
    showPanel(true, false);
    const term = createView();
    select(term);
    const workspace = typeof agentWorkspace !== "undefined" ? agentWorkspace?.key : undefined;
    try {
      const response = await api("open", { cols: term.instance.cols, rows: term.instance.rows, workspace });
      const { id, cwd } = await response.json();
      term.id = id;
      term.cwd = cwd;
      remember();
      renderTabs();
      fit(term);
      setTimeout(() => fit(term), 200);
      stream(term);
    } catch (error) {
      term.exited = true;
      term.instance.write(`\x1b[31m${error.message}\x1b[0m\r\n`);
      renderTabs();
    }
  }

  function closeTerminal(term) {
    term.closed = true;
    if (term.id && !term.exited) api("close", { id: term.id }).catch(() => {});
    term.instance.dispose();
    term.element.remove();
    const index = terminals.indexOf(term);
    terminals.splice(index, 1);
    remember();
    if (!terminals.length) {
      active = null;
      showPanel(false);
      return;
    }
    select(terminals[Math.min(index, terminals.length - 1)]);
  }

  function showPanel(show, spawn = true) {
    panel.hidden = !show;
    toggleButton.setAttribute("aria-pressed", String(show));
    store.set("pipkin-terminal-open", show);
    if (show && spawn && !terminals.length) newTerminal();
    else if (show) select(active || terminals[0] || null);
    else document.getElementById("prompt")?.focus({ preventScroll: true });
  }

  function togglePanel() {
    if (panel.hidden || !panel.contains(document.activeElement)) {
      if (panel.hidden) showPanel(true);
      else active?.instance.focus();
    } else {
      showPanel(false);
    }
  }

  /* Height: drag the top edge. */
  const resizer = byId("terminal-resize");
  const setHeight = (px) => {
    const height = Math.round(Math.max(140, Math.min(px, window.innerHeight * 0.75)));
    panel.style.height = height + "px";
    return height;
  };
  setHeight(store.get("pipkin-terminal-height", 260));
  resizer.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    resizer.setPointerCapture(event.pointerId);
    const startY = event.clientY;
    const startHeight = panel.offsetHeight;
    const move = (moveEvent) => {
      setHeight(startHeight + startY - moveEvent.clientY);
      scheduleFit();
    };
    const up = () => {
      resizer.removeEventListener("pointermove", move);
      store.set("pipkin-terminal-height", panel.offsetHeight);
    };
    resizer.addEventListener("pointermove", move);
    resizer.addEventListener("pointerup", up, { once: true });
    resizer.addEventListener("pointercancel", up, { once: true });
  });

  new ResizeObserver(scheduleFit).observe(host);
  const retheme = () => { for (const term of terminals) term.instance.options.theme = theme(); };
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme);
  new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  toggleButton.addEventListener("click", togglePanel);
  byId("terminal-new").addEventListener("click", newTerminal);
  byId("terminal-hide").addEventListener("click", () => showPanel(false));
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && !event.metaKey && !event.altKey && (event.key === "`" || event.code === "Backquote")) {
      event.preventDefault();
      togglePanel();
    }
  }, true);

  /* Reattach to shells that were running before a reload. */
  const saved = store.session();
  if (saved.length && window.Terminal) {
    for (const item of saved) {
      const term = createView(item);
      stream(term);
    }
    showPanel(Boolean(store.get("pipkin-terminal-open", false)), false);
    select(terminals[0]);
    if (panel.hidden) for (const term of terminals) term.element.hidden = true;
  }

  /* Browser pane (native, only inside the Pipkin app). */
  browserButton.hidden = !shell;
  browserButton.addEventListener("click", () => shell?.postMessage({ type: "browser", action: "toggle" }));

  /** Shows a terminal the agent opened on the server. */
  function attachTerminal(id) {
    if (typeof id !== "string" || terminals.some((term) => term.id === id)) return;
    showPanel(true, false);
    const term = createView({ id, title: "agent" });
    select(term);
    remember();
    stream(term);
    setTimeout(() => fit(term), 200);
  }

  /* Agent browser requests: the shell does the work and calls browserReply, which posts to the server. */
  const browserRuns = new Map();
  function browserRequest(runId, part) {
    if (!shell) {
      fetch("/api/agent/browser", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run: runId, id: part.id, error: "The browser pane only exists in the Pipkin app." }),
      }).catch(() => {});
      return;
    }
    browserRuns.set(part.id, runId);
    shell.postMessage({ type: "browser", action: "agent", id: part.id, op: part.op, url: part.url || "" });
  }
  function browserReply(result) {
    const runId = browserRuns.get(result?.id);
    if (!runId) return;
    browserRuns.delete(result.id);
    fetch("/api/agent/browser", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run: runId, id: result.id, title: result.title || "", url: result.url || "",
        text: String(result.text || "").slice(0, 45000), error: result.error || "" }),
    }).catch(() => {});
  }

  window.PipkinPanels = {
    toggleTerminal: togglePanel,
    newTerminal,
    attachTerminal,
    browserRequest,
    browserReply,
    hasBrowser: Boolean(shell),
    /** The terminal the user is looking at, for the agent's terminal tools. */
    activeTerminalId: () => (active && !active.exited && !panel.hidden ? active.id : terminals.find((term) => !term.exited)?.id) || null,
    /** Called by the app shell when the browser pane opens or closes. */
    browserState: (open) => browserButton.setAttribute("aria-pressed", String(Boolean(open))),
    openInBrowser: (url) => shell?.postMessage({ type: "browser", action: "open", url }),
    get terminals() { return terminals.map(({ id, title, exited, instance }) => ({ id, title, exited, cols: instance.cols, rows: instance.rows })); },
  };
})();
