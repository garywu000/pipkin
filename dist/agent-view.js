"use strict";

/* Agent run state + timeline rendering. */
(() => {
  const { icon, el, formatDuration } = window.PipkinUI;

  const TOOL_ICONS = {
    list_files: "folder", search_text: "search", read_file: "file", read_files: "file", outline_file: "file", write_file: "edit", edit_file: "edit",
    run_command: "terminal", write_page: "edit", web_search: "globe", web_fetch: "globe", remember: "brain", update_plan: "list",
    terminal_run: "terminal", terminal_read: "terminal", browser_open: "globe", browser_read: "globe",
    map_document: "file", check_page: "check",
  };
  const MODE_LABELS = { read: "Read only", ask: "Ask first", auto: "Auto-edit" };

  function createRun(meta = {}) {
    return {
      version: 2, state: "working", startedAt: Date.now(), endedAt: null,
      workspace: meta.workspace || null, workspacePath: meta.workspacePath || null,
      mode: meta.mode || "ask", web: Boolean(meta.web),
      plan: [], entries: [], files: [], draft: "", answer: "", usage: null, runId: null, checkpointId: meta.checkpointId || null,
    };
  }

  function lastLiveThinking(run) {
    return run.entries.findLast((entry) => entry.type === "thinking" && entry.live);
  }

  function endThinking(run) {
    const live = lastLiveThinking(run);
    if (live) {
      live.live = false;
      live.ms = (live.ms || 0) + (Date.now() - (live.segmentStart || live.startedAt || Date.now()));
    }
  }

  function thinkingMs(entry) {
    if (Number.isFinite(entry.ms)) return entry.ms;
    return entry.endedAt && entry.startedAt ? entry.endedAt - entry.startedAt : 0;
  }

  function settleDraft(run) {
    endThinking(run);
    if (run.draft.trim()) run.entries.push({ type: "text", text: run.draft.trim() });
    run.draft = "";
  }

  function showSavedFiles(run, paths) {
    const files = run.files ||= [];
    for (const path of paths) {
      if (files.includes(path)) continue;
      files.push(path);
      run.entries.push({ type: "text", text: "Saved “" + path + "” to the workspace." });
    }
  }

  /** Apply one streamed server event. Returns extra info for the caller. */
  function apply(run, event) {
    switch (event.event) {
      case "run":
        run.runId = event.id;
        if (event.workspace) run.workspace = event.workspace;
        if (event.workspacePath) run.workspacePath = event.workspacePath;
        run.notes = event.notes || null;
        run.mode = event.mode || run.mode;
        break;
      case "checkpoint_restored":
        run.plan = event.plan;
        for (const action of event.activity) {
          if (action.type === "text") {
            run.entries.push({ type: "text", text: action.text });
            continue;
          }
          run.entries.push({ type: "tool", id: action.id, name: action.name, label: action.label, output: action.preview,
            state: action.cancelled ? "cancelled" : action.ok === true ? "ok" : action.ok === null ? "declined" : "fail" });
        }
        for (const label of event.uncertain) {
          run.entries.push({ type: "text", text: "Interrupted action to review: " + label });
        }
        showSavedFiles(run, event.files || []);
        break;
      case "step":
        endThinking(run);
        run.draft = "";
        break;
      case "model_step":
        run.modelPhase = event.planning ? "Planning" : "Preparing the next action";
        break;
      case "thinking": {
        let entry = lastLiveThinking(run);
        const last = run.entries.at(-1);
        if (!entry && last && last.type === "thinking") {
          // Consecutive model turns with only a hidden plan update between them: keep one "Thought" row.
          entry = last;
          entry.live = true;
          entry.segmentStart = Date.now();
          if (entry.text.trim()) entry.text += "\n\n";
        }
        if (!entry) {
          entry = { type: "thinking", text: "", live: true, open: false, ms: 0, segmentStart: Date.now() };
          run.entries.push(entry);
        }
        entry.text += event.text;
        break;
      }
      case "delta":
        endThinking(run);
        run.draft += event.text;
        break;
      case "tool":
        settleDraft(run);
        if (event.name === "update_plan") break;
        run.entries.push({ type: "tool", id: event.id, name: event.name, label: event.label, state: "running", output: "", diff: "", open: false });
        break;
      case "tool_result": {
        const entry = run.entries.find((item) => item.type === "tool" && item.id === event.id);
        if (entry) {
          entry.state = event.cancelled ? "cancelled" : event.ok === true ? "ok" : event.ok === null ? "declined" : "fail";
          entry.output = event.preview || "";
        }
        if (event.saved_file) showSavedFiles(run, [event.saved_file]);
        break;
      }
      case "diff": {
        const entry = run.entries.find((item) => item.type === "tool" && item.id === event.id);
        if (entry) entry.diff = event.text;
        break;
      }
      case "approval":
        settleDraft(run);
        run.entries.push({ type: "approval", id: event.id, kind: event.kind, title: event.title, detail: event.detail, decision: null });
        return { approval: true };
      case "approval_result": {
        const entry = run.entries.find((item) => item.type === "approval" && item.id === event.id);
        if (entry) entry.decision = event.cancelled ? "expired" : event.approved ? "approved" : "denied";
        break;
      }
      case "plan":
        run.plan = (event.steps || []).map((step) => ({ text: step.text, status: step.status }));
        break;
      case "memory":
        settleDraft(run);
        run.entries.push({ type: "memory", text: event.text });
        return { memory: event.text };
      case "progress":
      case "notice":
        endThinking(run);
        run.draft = "";
        run.entries.push({ type: "text", text: event.text });
        break;
      case "steering_queued": {
        const entry = run.entries.find((item) => item.type === "steering" && item.id === event.id);
        if (!entry) run.entries.push({ type: "steering", id: event.id, text: event.text, state: "sending" });
        else if (entry.state === "sending") entry.state = "queued";
        break;
      }
      case "steering_failed":
        run.entries = run.entries.filter((entry) => entry.type !== "steering" || entry.id !== event.id);
        break;
      case "steering":
        endThinking(run);
        run.draft = "";
        {
          const entry = run.entries.find((item) => item.type === "steering" && item.id === event.id);
          if (entry) entry.state = "applied";
          else run.entries.push({ type: "steering", id: event.id, text: event.text, state: "applied" });
        }
        break;
      case "result":
        endThinking(run);
        run.answer = event.text;
        run.draft = "";
        run.state = "complete";
        run.endedAt = Date.now();
        return { final: event.text };
      case "stopped":
        if (event.text) run.draft = "";
        showSavedFiles(run, event.files || []);
        finish(run, "stopped", event.text || "Stopped after the current step.");
        return { final: run.answer };
      case "usage":
        run.usage = { tokens: event.tokens, seconds: event.seconds, steps: event.steps, compactions: event.compactions, timings: event.timings };
        break;
      default:
        break;
    }
    return {};
  }

  function finish(run, state, message) {
    run.state = state;
    run.endedAt = Date.now();
    endThinking(run);
    if (run.draft.trim() && state !== "complete") settleDraft(run);
    for (const entry of run.entries) {
      if (entry.type === "tool" && entry.state === "running") entry.state = "fail";
      if (entry.type === "approval" && !entry.decision) entry.decision = "expired";
    }
    if (message) run.answer = message;
  }

  /* ---------------- rendering ---------------- */

  const spinner = () => PipkinUI.spinner();

  function statusIcon(state) {
    if (state === "running") return el("span", { className: "step-icon" }, spinner());
    if (state === "waiting") return el("span", { className: "step-icon" }, icon("shield"));
    const declined = state === "declined" || state === "cancelled";
    const cls = declined ? "declined" : state === "fail" ? "fail" : "ok";
    const name = declined ? "x" : state === "fail" ? "alert" : "check";
    return el("span", { className: "step-icon " + cls }, icon(name));
  }

  function diffView(text) {
    const pre = el("div", { className: "tool-output diff" });
    for (const line of text.split("\n")) {
      const cls = line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "";
      pre.append(el("span", { className: cls, text: line || " " }));
    }
    return pre;
  }

  function commandResult(entry) {
    const output = entry.output || "";
    if (/\[Command timed out[^\]\n]*\]\s*$/.test(output)) return "timed out";
    const exit = output.match(/\[exit code (-?\d+)\]\s*$/);
    return exit ? "exit " + exit[1] : "";
  }

  function actionState(entry) {
    if (entry.state === "ok" && entry.name === "run_command") {
      const result = commandResult(entry);
      if (result && result !== "exit 0") return "fail";
    }
    return entry.state;
  }

  /** A short, human summary of a tool result, shown at the end of its row. */
  function toolMeta(entry) {
    const out = entry.output || "";
    if (entry.state === "running") return null;
    if (entry.state === "cancelled") return el("span", { className: "tool-meta", text: "canceled" });
    if (entry.state === "declined") return el("span", { className: "tool-meta", text: "declined" });
    if (out.startsWith("Tool error:")) return el("span", { className: "tool-meta fail", title: out, text: out.replace("Tool error: ", "") });
    let match;
    switch (entry.name) {
      case "read_file":
        match = out.match(/showing lines (\d+)-(\d+) of (\d+)/);
        return match ? el("span", { className: "tool-meta", text: `lines ${match[1]}–${match[2]} of ${match[3]}` }) : null;
      case "outline_file":
        match = out.match(/(\d+) lines, (\d+) outline entries/);
        return match ? el("span", { className: "tool-meta", text: `${match[2]} entries · ${match[1]} lines` }) : null;
      case "search_text": {
        if (out.startsWith("(No matches")) return el("span", { className: "tool-meta", text: "no matches" });
        const count = out.split("\n").filter((line) => /^[^\s].*:\d+:|\(file name match\)/.test(line)).length;
        return el("span", { className: "tool-meta", text: `${count} ${count === 1 ? "match" : "matches"}` });
      }
      case "list_files": {
        const count = out.split("\n").filter((line) => line.trim() && !line.startsWith("…")).length;
        return el("span", { className: "tool-meta", text: `${count} items` });
      }
      case "write_file":
      case "edit_file":
      case "write_page":
        match = out.match(/\(\+(\d+) −(\d+) lines\)/);
        return match ? el("span", { className: "tool-meta" }, el("b", { className: "add", text: "+" + match[1] }), " ", el("b", { className: "del", text: "−" + match[2] })) : null;
      case "run_command": {
        const result = commandResult(entry);
        return result ? el("span", { className: "tool-meta " + (result === "exit 0" ? "good" : "fail"), text: result }) : null;
      }
      case "web_search": {
        const count = (out.match(/^(?:\[S\d+\]|\d+\.) /gm) || []).length;
        return count ? el("span", { className: "tool-meta", text: `${count} results` }) : null;
      }
      case "web_fetch":
        match = out.match(/^Title: (.+)$/m);
        return match ? el("span", { className: "tool-meta", title: match[1], text: match[1] }) : null;
      default:
        return null;
    }
  }

  function toolEntry(entry, onChange, awaiting = false) {
    const details = el("details", { className: "tool-step", open: entry.open });
    details.addEventListener("toggle", () => { entry.open = details.open; onChange?.(); });
    const text = entry.label.replace(/ · lines .*$/, "");
    const space = text.indexOf(" ");
    const verb = space > 0 ? text.slice(0, space) : text;
    const target = space > 0 ? text.slice(space + 1) : "";
    const label = el("span", { className: "tool-label" }, el("span", { className: "tool-verb", text: verb }), target ? " " : null, target ? el("code", { text: target }) : null);
    const meta = el("span", { className: "tool-meta-slot" });
    details.append(el("summary", {},
      el("span", { className: "tool-kind" }, icon(TOOL_ICONS[entry.name] || "sparkle")),
      label, meta, icon("chevron-right", "icon chev-sm")));
    const content = el("div");
    details.append(content);
    const node = el("li", { className: "step-tool" }, statusIcon(entry.state), details);
    let state, diff, output;
    const update = (waiting) => {
      const nextState = actionState(entry);
      const nextOutput = entry.output || (!entry.diff && entry.state === "running" && !waiting ? "Running…" : "");
      if (state === nextState && diff === entry.diff && output === nextOutput) return;
      if (state !== nextState) {
        state = nextState;
        node.dataset.state = state;
        node.firstChild.replaceWith(statusIcon(state));
      }
      diff = entry.diff;
      output = nextOutput;
      const tag = toolMeta(entry);
      meta.replaceChildren(...(tag ? [tag] : []));
      content.replaceChildren(...[
        diff ? diffView(diff) : null,
        output ? el("div", { className: "tool-output", text: output }) : null,
      ].filter(Boolean));
    };
    update(awaiting);
    return { node, update };
  }

  function approvalCard(entry, onApprove) {
    const card = el("div", { className: "approval-card", dataset: { decision: entry.decision || "pending" } });
    const heading = entry.decision === "approved" ? "Approved" : entry.decision === "denied" ? "Declined" : entry.decision === "expired" ? "Not answered" : "Needs your approval";
    card.append(el("header", {}, icon(entry.decision === "approved" ? "check" : entry.decision ? "x" : entry.kind === "command" ? "terminal" : "edit"), el("span", { text: `${heading} · ${entry.title}` })));
    // Once decided, the approval is shown beside the action's result inside its batch.
    if (entry.decision) return card;
    card.append(entry.kind === "edit" ? diffView(entry.detail) : el("div", { className: "tool-output", text: "$ " + entry.detail }));
    const footer = el("footer");
    if (!entry.decision) {
      const deny = el("button", { type: "button", className: "button small", text: "Deny" });
      const allow = el("button", { type: "button", className: "button small accent", text: entry.kind === "command" ? "Run command" : "Apply change" });
      deny.addEventListener("click", () => onApprove?.(entry, false));
      allow.addEventListener("click", () => onApprove?.(entry, true));
      footer.append(el("span", { className: "decision", text: entry.kind === "command" ? (/your terminal/i.test(entry.title || "") ? "Types into your terminal panel." : "Runs in your workspace folder.") : "Writes to your workspace folder." }), deny, allow);
    }
    card.append(footer);
    return card;
  }

  function thoughtPreview(text, live) {
    const lines = text.trim().split(/\n+/).map((line) => line.trim()).filter(Boolean);
    const line = (live ? lines.at(-1) : lines[0]) || "";
    return line.replace(/[*_`#>]/g, "").slice(0, 200);
  }

  function thinkingEntry(entry, onChange) {
    const details = el("details", { className: "thinking-step", open: entry.open });
    details.addEventListener("toggle", () => { entry.open = details.open; onChange?.(); });
    const label = el("span", { className: "label" });
    const preview = el("span", { className: "thought-preview" });
    const header = el("summary", {}, label, preview, icon("chevron-right", "icon chev-sm"));
    const text = document.createTextNode("");
    details.append(header, el("div", { className: "reasoning-text", tabindex: 0 }, text));
    const glyph = el("span", { className: "step-icon" });
    const node = el("li", { className: "step-thinking" }, glyph, details);
    let live;
    const update = () => {
      if (entry.live !== live) {
        live = entry.live;
        glyph.replaceChildren(live ? spinner() : icon("bulb"));
        header.classList.toggle("live", live);
        const ms = thinkingMs(entry);
        label.textContent = live ? "Thinking" : ms >= 1000 ? `Thought for ${formatDuration(ms)}` : "Thought";
      }
      if (text.data !== entry.text) {
        if (entry.text.startsWith(text.data)) text.appendData(entry.text.slice(text.length));
        else text.data = entry.text;
        preview.textContent = thoughtPreview(entry.text, entry.live);
      }
    };
    update();
    return { node, update };
  }

  function legacyEntry(entry) {
    return el("li", {}, statusIcon(entry.kind === "blocked" ? "fail" : "ok"), el("div", { className: "legacy-entry" }, el("b", { text: entry.kind }), entry.text));
  }

  function currentActivity(run) {
    if (run.entries.some((entry) => entry.type === "approval" && !entry.decision)) return "Waiting for your approval";
    if (lastLiveThinking(run)) return "Thinking";
    if (run.entries.findLast((entry) => entry.type === "tool")?.state === "running") return "Running an action";
    if (run.draft.trim()) return "Writing the answer";
    return run.modelPhase || "Working";
  }

  function summaryText(run, now) {
    const tools = run.entries.filter((entry) => entry.type === "tool").length;
    const elapsed = formatDuration((run.endedAt || now) - run.startedAt, run.state !== "working");
    const steps = tools ? ` · ${tools} ${tools === 1 ? "action" : "actions"}` : "";
    if (run.state === "working") return `${currentActivity(run)} · ${elapsed}${steps}`;
    if (run.state === "stopped") return `Stopped after ${elapsed}${steps}`;
    if (run.state === "error") return `Stopped with an error · ${elapsed}${steps}`;
    return `Worked for ${elapsed}${steps}`;
  }

  function planCard(steps, title, working) {
    const done = steps.filter((step) => step.status === "completed").length;
    const percent = Math.round((done / steps.length) * 100);
    return el("div", { className: "plan-card" },
      el("h4", {}, icon("list"), el("span", { text: title }), el("span", { className: "plan-progress", text: `${done} of ${steps.length}` })),
      el("div", { className: "plan-bar", role: "progressbar", "aria-valuenow": String(percent), "aria-valuemin": "0", "aria-valuemax": "100" }, el("i", { style: `width:${percent}%` })),
      el("ol", {}, steps.map((step) => el("li", { dataset: { status: step.status } },
        el("span", { className: "box" }, step.status === "completed" ? icon("check") : step.status === "in_progress" && working ? spinner() : null),
        el("span", { text: step.text })))));
  }

  function syncChildren(parent, nodes) {
    for (const [index, node] of nodes.entries()) {
      const current = parent.children[index] || null;
      if (current !== node) parent.insertBefore(node, current);
    }
    while (parent.children.length > nodes.length) parent.lastElementChild.remove();
  }

  function mount(run, { onApprove, onChange } = {}) {
    const root = el("div", { className: "agent-run" });
    if (run.version !== 2) {
      const legacy = el("details", { className: "tool-step" },
        el("summary", {}, icon("bot"), el("span", { text: "Agent activity" })),
        el("ol", { className: "timeline" }, (run.entries || []).map(legacyEntry)));
      root.append(legacy);
      return { element: root, render() {}, collapse() { legacy.open = false; } };
    }
    let open = true;
    let timer = 0;
    let state, badgesKey, planKey, planState;
    const rows = new WeakMap();
    const groups = new WeakMap();
    const glyph = el("span", { className: "agent-summary-icon" });
    const label = el("span", { className: "summary-text" });
    const summary = el("button", { type: "button", className: "agent-summary" }, glyph, label, icon("chevron-right", "icon chev"));
    const badges = el("div", { className: "agent-badges" });
    const plan = el("div");
    const timeline = el("ol", { className: "timeline" });
    const body = el("div", { className: "agent-body" }, plan, timeline);
    const busyText = el("span");
    const busyRow = el("li", { className: "step-now" }, el("span", { className: "step-icon" }, spinner()), busyText);
    root.append(el("div", { className: "agent-head" }, summary, badges), body);
    summary.addEventListener("click", () => { open = root.dataset.open !== "true"; render(); });

    function rowFor(entry, waiting = false) {
      let row = rows.get(entry);
      if (!row) {
        if (entry.type === "tool") row = toolEntry(entry, onChange);
        else if (entry.type === "thinking") row = thinkingEntry(entry, onChange);
        else if (entry.type === "approval") {
          const node = el("li", { className: "step-approval" });
          const card = el("div");
          let decision = "";
          const update = () => {
            const next = entry.decision || null;
            if (decision === next) return;
            decision = next;
            card.replaceChildren(approvalCard(entry, onApprove));
            if (card.parentNode === node) node.firstChild.replaceWith(statusIcon(decision === "approved" ? "ok" : decision ? "declined" : "running"));
          };
          node.append(statusIcon("running"), card);
          row = { node, card, update };
        } else if (entry.type === "steering") {
          const status = el("small");
          const node = el("li", { className: "step-steering" }, el("div", { className: "steering-message" },
            el("header", {}, el("strong", { text: "You" }), status), el("p", { text: entry.text })));
          const update = () => {
            const text = entry.state === "sending" ? "Sending…" : entry.state === "queued"
              ? run.state === "working" ? "Queued for next step" : "Queued in Work checkpoint" : "Steered the run";
            if (status.textContent !== text) status.textContent = text;
          };
          row = { node, update };
        } else if (entry.type === "text" || entry.type === "memory") {
          const info = entry.type === "memory";
          const node = el("li", { className: "step-note" },
            el("span", { className: "step-icon note" }, icon(info ? "brain" : "chat")),
            el("div", { className: "agent-note" }, info ? "Saved to memory: " : null, entry.text));
          row = { node };
        } else row = { node: legacyEntry(entry) };
        rows.set(entry, row);
      }
      row.update?.(waiting);
      return row;
    }

    function groupFor(entries) {
      const first = entries[0];
      let group = groups.get(first.entry);
      if (!group) {
        const title = el("span");
        const outcome = el("span", { className: "batch-outcome" });
        const list = el("ol", { className: "timeline batch-timeline" });
        const details = el("details", { className: "tool-step batch-step" },
          el("summary", {}, title, outcome, icon("chevron-right", "icon chev-sm")), list);
        const node = el("li", { className: "step-batch" }, statusIcon("running"), details);
        group = { node, list, title, outcome };
        groups.set(first.entry, group);
      }
      const active = entries.some(({ entry, waiting }) => entry.state === "running" && !waiting);
      const failed = entries.filter(({ entry }) => actionState(entry) === "fail").length;
      const blocked = entries.filter(({ entry }) => ["declined", "cancelled"].includes(entry.state)).length;
      const waiting = entries.some((action) => action.waiting);
      const phase = active ? "running" : failed ? "fail" : waiting ? "waiting" : blocked ? "declined" : "ok";
      if (phase !== group.phase) {
        group.phase = phase;
        group.node.firstChild.replaceWith(statusIcon(phase));
      }
      const title = (active ? "Running " : "") + entries.length + (entries.length === 1 ? " action" : " actions");
      if (group.title.textContent !== title) group.title.textContent = title;
      const outcome = [failed && `${failed} failed`, blocked && `${blocked} skipped`].filter(Boolean).join(" · ");
      if (group.outcome.textContent !== outcome) group.outcome.textContent = outcome;
      group.outcome.dataset.level = failed ? "error" : "info";
      syncChildren(group.list, entries.map(({ node }) => node));
      return group.node;
    }

    function render() {
      const pending = run.entries.some((entry) => entry.type === "approval" && !entry.decision);
      const isOpen = open || pending;
      root.dataset.open = String(isOpen);
      body.hidden = badges.hidden = !isOpen;
      summary.setAttribute("aria-expanded", String(isOpen));
      if (state !== run.state) {
        state = run.state;
        root.dataset.state = summary.dataset.state = state;
        glyph.replaceChildren(state === "working" ? spinner() : icon(state === "complete" ? "check" : "alert"));
        if (state !== "working") clearInterval(timer);
      }
      label.textContent = summaryText(run, Date.now());
      if (!isOpen) return;
      const badgeData = [run.workspace, run.mode, run.notes, run.web, run.usage?.tokens, run.usage?.steps];
      const nextBadges = JSON.stringify(badgeData);
      if (nextBadges !== badgesKey) {
        badgesKey = nextBadges;
        badges.replaceChildren(...[
          el("span", { className: "badge" }, icon("folder"), run.workspace || "No folder"),
          run.workspace ? el("span", { className: "badge" }, icon("shield"), MODE_LABELS[run.mode] || run.mode) : null,
          run.notes ? el("span", { className: "badge", title: "Standing instructions loaded from the workspace" }, icon("note"), run.notes) : null,
          el("span", { className: "badge" }, icon("globe"), run.web ? "Web" : "No web"),
          run.usage?.steps ? el("span", { className: "badge", title: (run.usage.compactions || 0) + " context compactions" }, run.usage.steps + " model steps") : null,
        ].filter(Boolean));
      }
      const startedTools = run.entries.some((entry) => entry.type === "tool");
      const phase = state === "complete" ? 3 : startedTools ? 1 : 0;
      const nextPlan = run.plan.length ? run.plan : phase;
      if (nextPlan !== planKey || planState !== state) {
        planKey = nextPlan;
        planState = state;
        const actual = run.plan.length > 0;
        const steps = actual ? run.plan : ["Review the request", "Work through the task", "Return the result"].map((text, index) =>
          ({ text, status: index < phase ? "completed" : index === phase ? "in_progress" : "pending" }));
        plan.replaceChildren(planCard(steps, actual ? "Plan" : "Run checklist", state === "working"));
      }

      let batch = [];
      let lastToolRow;
      const lastTool = run.entries.findLast((entry) => entry.type === "tool");
      const items = [];
      const flush = () => { if (batch.length) items.push(groupFor(batch)); batch = []; };
      for (const entry of run.entries) {
        const waiting = pending && entry === lastTool;
        const row = rowFor(entry, waiting);
        if (entry.type === "tool") {
          lastToolRow = row.node;
          batch.push({ entry, node: row.node, waiting });
          continue;
        }
        if (entry.type === "approval" && entry.decision && lastToolRow) {
          if (row.card.parentNode !== lastToolRow) lastToolRow.append(row.card);
          continue;
        }
        // Pending approvals stay actionable outside closed batches. Notes and steering end a batch.
        flush();
        items.push(row.node);
      }
      flush();
      if (state === "working" && !pending && !lastLiveThinking(run) && lastTool?.state !== "running" && !run.draft.trim()) {
        const nextText = (run.modelPhase || "Working") + "…";
        if (busyText.textContent !== nextText) busyText.textContent = nextText;
        items.push(busyRow);
      }
      syncChildren(timeline, items);
    }

    render();
    if (run.state === "working") timer = setInterval(() => {
      if (!root.isConnected || run.state !== "working") clearInterval(timer);
      else label.textContent = summaryText(run, Date.now());
    }, 1000);
    return {
      element: root,
      render,
      collapse() { open = false; render(); },
    };
  }

  window.PipkinAgent = { createRun, apply, finish, mount, MODE_LABELS };
})();
