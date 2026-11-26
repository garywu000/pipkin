/* Scheduled background tasks: the sidebar entry, the Scheduled window (inbox and task list), and the task editor.
   The server runs the tasks; this page shows what they did and lets you add, change, run, stop, and undo them. */
(() => {
  const { el, icon, toast, copyText, renderMarkdown } = window.PipkinUI;
  const byId = (id) => document.getElementById(id);
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];          // Python weekday() order
  const ACCESS_LABELS = { read: "Read only", create: "Creates new files" };
  const STATUS = {
    complete: { label: "Done", glyph: "check" },
    stopped: { label: "Stopped", glyph: "stop" },
    error: { label: "Failed", glyph: "alert" },
    skipped: { label: "Skipped", glyph: "clock" },
  };

  let options = {};
  let state = { tasks: [], inbox: [], running: null, queued: [], paused: false, unread: 0, active: true };
  let seenInbox = null;          // inbox ids already known, so only new finishes are announced
  let pollTimer = 0;
  let requestVersion = 0;
  let pendingActions = 0;
  let view = "inbox";
  let editing = null;            // the task being edited, or null for a new one
  let editorWorkspace = null;    // {key, name} when a folder was chosen for this task
  let deleteArmed = false;
  const expanded = new Set();

  const dialog = () => byId("schedule-dialog");
  const editor = () => byId("schedule-editor");

  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || "Pipkin couldn't update scheduled tasks.");
      error.code = data.code;
      throw error;
    }
    return data;
  }

  /* ---------- formatting ---------- */

  function clock(hhmm) {
    const [hours, minutes] = hhmm.split(":").map(Number);
    return new Date(2000, 0, 1, hours, minutes).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function moment(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    const today = new Date();
    const day = (offset) => new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset).toDateString();
    const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (date.toDateString() === day(0)) return `Today ${time}`;
    if (date.toDateString() === day(1)) return `Tomorrow ${time}`;
    if (date.toDateString() === day(-1)) return `Yesterday ${time}`;
    return `${date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}, ${time}`;
  }

  function describe(schedule) {
    const at = clock(schedule.time);
    if (schedule.kind === "once") {
      const [year, month, date] = schedule.date.split("-").map(Number);
      return `Once, ${new Date(year, month - 1, date).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} at ${at}`;
    }
    if (schedule.kind === "daily") return `Every day at ${at}`;
    if (schedule.kind === "weekdays") return `Weekdays at ${at}`;
    return `Every ${(schedule.days || []).map((day) => DAYS[day]).join(", ")} at ${at}`;
  }

  /* ---------- state ---------- */

  async function refresh() {
    if (pendingActions) {
      schedulePoll();
      return;
    }
    const version = ++requestVersion;
    try {
      const next = await api("/api/schedules");
      if (version === requestVersion) apply(next);
    } catch {
      // The server may be restarting; keep polling after a failed request.
      if (version === requestVersion) schedulePoll();
    }
  }

  function apply(next) {
    const before = seenInbox;
    const changed = JSON.stringify(state) !== JSON.stringify({ ...state, ...next });
    state = { ...state, ...next };
    const ids = new Set(state.inbox.map((item) => item.id));
    if (before) {
      const fresh = state.inbox.filter((item) => !before.has(item.id) && !item.read);
      if (fresh.length === 1) {
        const item = fresh[0];
        const label = STATUS[item.status]?.label || "Finished";
        toast(`Scheduled task ${label.toLowerCase()}: “${item.title}”`);
        options.notifyShell?.({ type: "finished", title: `Scheduled task ${label.toLowerCase()}`, body: item.title });
      } else if (fresh.length > 1) {
        toast(`${fresh.length} scheduled tasks finished`);
        options.notifyShell?.({ type: "finished", title: `${fresh.length} scheduled tasks finished`, body: fresh[0].title });
      }
    }
    seenInbox = ids;
    renderSidebar();
    if (changed && dialog()?.open) render();
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    const fast = state.running || dialog()?.open;
    pollTimer = setTimeout(refresh, fast ? 4000 : document.hidden ? 60000 : 20000);
  }

  function runningTitle() {
    return state.running?.title || "";
  }

  async function act(path, body, { quiet = false } = {}) {
    const version = ++requestVersion;
    pendingActions++;
    try {
      const next = await api(path, body);
      if (version === requestVersion) apply(next);
      return true;
    } catch (error) {
      if (!quiet) toast(error.message);
      return false;
    } finally {
      if (--pendingActions === 0) void refresh();
    }
  }

  /* ---------- sidebar ---------- */

  function renderSidebar() {
    const badge = byId("scheduled-badge");
    const unread = state.inbox.filter((item) => !item.read).length;
    badge.hidden = !unread;
    badge.textContent = unread > 9 ? "9+" : String(unread);
    byId("scheduled-running").hidden = !state.running;
    const button = byId("open-scheduled");
    button.title = state.running ? `Running “${state.running.title}”` : unread ? `${unread} finished ${unread === 1 ? "task" : "tasks"} waiting` : "Scheduled tasks";
  }

  /* ---------- the Scheduled window ---------- */

  function open(nextView) {
    if (nextView) view = nextView;
    document.querySelector(`input[name="schedule-view"][value="${view}"]`).checked = true;
    if (!dialog().open) dialog().showModal();
    render();
    void refresh();
  }

  function render() {
    byId("schedule-paused").checked = Boolean(state.paused);
    const unread = state.inbox.filter((item) => !item.read).length;
    byId("schedule-inbox-count").textContent = unread ? String(unread) : "";
    byId("schedule-task-count").textContent = state.tasks.length ? String(state.tasks.length) : "";
    byId("schedule-read-all").hidden = view !== "inbox" || !unread;
    byId("schedule-inbox").hidden = view !== "inbox";
    byId("schedule-tasks").hidden = view !== "tasks";
    renderRunning();
    if (view === "inbox") renderInbox();
    else renderTasks();
  }

  function renderRunning() {
    const box = byId("schedule-running");
    const running = state.running;
    box.hidden = !running && !state.paused && state.active !== false;
    if (box.hidden) {
      box.replaceChildren();
      return;
    }
    if (running) {
      const stop = el("button", { type: "button", className: "button small", text: "Stop" });
      stop.addEventListener("click", () => {
        stop.disabled = true;
        void act("/api/schedules/stop", {});
      });
      box.dataset.kind = "running";
      box.replaceChildren(el("span", { className: "schedule-spinner", "aria-hidden": "true" }),
        el("span", { text: `Running “${running.title}” · started ${moment(running.startedAt)}` }), stop);
    } else if (state.paused) {
      box.dataset.kind = "paused";
      box.replaceChildren(icon("clock"), el("span", { text: "Scheduled tasks are paused. Nothing new starts until you turn Pause all off." }));
    } else {
      box.dataset.kind = "paused";
      box.replaceChildren(icon("alert"), el("span", { text: "Another Pipkin window is running scheduled tasks." }));
    }
  }

  function emptyNote(text, action) {
    return el("div", { className: "schedule-empty" }, icon("clock"), el("p", { text }), action || null);
  }

  function renderInbox() {
    const list = byId("schedule-inbox");
    if (!state.inbox.length) {
      list.replaceChildren(emptyNote("Nothing here yet. When a scheduled task finishes, its summary and any new files land here."));
      return;
    }
    list.replaceChildren(...state.inbox.map(inboxItem));
    for (const item of list.children) {
      const summary = item.querySelector(".inbox-summary");
      const more = item.querySelector(".inbox-more");
      const clipped = summary.scrollHeight > summary.clientHeight + 1;
      more.hidden = !clipped && !summary.classList.contains("open");
      summary.classList.toggle("clipped", clipped);
    }
  }

  function inboxItem(item) {
    const status = STATUS[item.status] || STATUS.error;
    const node = el("article", { className: "inbox-item" + (item.read ? "" : " unread"), dataset: { status: item.status } });
    const summary = el("div", { className: "inbox-summary md" + (expanded.has(item.id) ? " open" : "") });
    renderMarkdown(summary, item.summary || (item.status === "complete" ? "Finished." : status.label + "."));
    const more = el("button", { type: "button", className: "inbox-more", text: expanded.has(item.id) ? "Show less" : "Show more" });
    more.addEventListener("click", () => {
      if (expanded.has(item.id)) expanded.delete(item.id);
      else expanded.add(item.id);
      markRead(item);
      render();
    });
    const files = (item.outputs || []).map((output) => {
      const chip = el("button", {
        type: "button", className: "inbox-file" + (output.undone ? " undone" : ""), title: output.undone ? "Undone" : `Open ${output.path}`,
        disabled: Boolean(output.undone),
      }, icon("file"), el("span", { text: output.path }));
      chip.addEventListener("click", () => {
        markRead(item);
        void api("/api/schedules/open", { id: item.id, path: output.path }).catch((error) => toast(error.message));
      });
      return chip;
    });
    const canUndo = (item.outputs || []).some((output) => output.undo && !output.undone);
    const checksNode = item.skillChecks?.results?.length ? (() => {
      const results = item.skillChecks.results;
      const passedCount = results.filter((c) => c.ok).length;
      return el("details", { className: "skill-checks-card inbox-checks", open: !item.skillChecks.passed },
        el("summary", {},
          el("span", { text: item.skillChecks.skill ? `Skill checks · /${item.skillChecks.skill}` : "Skill checks" }),
          el("span", { className: "skill-badge " + (item.skillChecks.passed ? "checks" : "warn"), text: `${passedCount}/${results.length} passed` }),
          icon("chevron-right", "icon chev-sm")),
        el("ul", {}, ...results.map((check) => el("li", { "data-ok": String(Boolean(check.ok)) },
          el("span", { className: "skill-check-mark", text: check.ok ? "✓" : "!" }),
          el("div", {}, el("span", { text: check.label }),
            check.detail ? el("span", { className: "skill-check-detail", text: check.detail }) : null)))));
    })() : null;
    const actions = [];
    if (canUndo) {
      const undo = el("button", { type: "button", className: "button small", text: "Undo run" });
      undo.addEventListener("click", async () => {
        undo.disabled = true;
        if (await act("/api/schedules/undo", { id: item.id })) toast("Removed the files this run created");
        else undo.disabled = false;
      });
      actions.push(undo);
    }
    if (item.summary) {
      const copy = el("button", { type: "button", className: "button small", text: "Copy" });
      copy.addEventListener("click", () => {
        markRead(item);
        void copyText(item.summary, copy);
      });
      actions.push(copy);
    }
    if (!item.read) {
      const read = el("button", { type: "button", className: "button small", text: "Mark read" });
      read.addEventListener("click", () => markRead(item));
      actions.push(read);
    }
    const dismiss = el("button", { type: "button", className: "icon-button small", title: "Dismiss", "aria-label": `Dismiss “${item.title}”` }, icon("x"));
    dismiss.addEventListener("click", () => void act("/api/schedules/dismiss", { id: item.id }));
    const meta = [moment(item.endedAt), item.folderName, item.trigger === "manual" ? "run by you" : ""].filter(Boolean).join(" · ");
    node.append(
      el("div", { className: "inbox-top" },
        el("span", { className: "inbox-status", title: status.label }, icon(status.glyph)),
        el("div", { className: "inbox-title" }, el("strong", { text: item.title }), el("span", { text: `${status.label} · ${meta}` })),
        dismiss),
      summary,
      more,
      ...(checksNode ? [checksNode] : []),
      ...(files.length ? [el("div", { className: "inbox-files" }, files)] : []),
      ...(actions.length ? [el("div", { className: "inbox-actions" }, actions)] : []));
    return node;
  }

  function markRead(item) {
    if (item.read) return;
    item.read = true;
    renderSidebar();
    void act("/api/schedules/read", { id: item.id }, { quiet: true });
  }

  function renderTasks() {
    const list = byId("schedule-tasks");
    if (!state.tasks.length) {
      const create = el("button", { type: "button", className: "button small", text: "New task" });
      create.addEventListener("click", () => openEditor(null));
      list.replaceChildren(emptyNote("No scheduled tasks yet. Try “Every Friday at 5 PM, turn this week's notes into a review sheet.”", create));
      return;
    }
    const sorted = [...state.tasks].sort((a, b) => (b.enabled - a.enabled) || String(a.nextRun || "~").localeCompare(String(b.nextRun || "~")));
    list.replaceChildren(...sorted.map(taskRow));
  }

  function taskRow(task) {
    const running = state.running?.taskId === task.id;
    const queued = (state.queued || []).includes(task.id);
    const details = [describe(task.schedule), task.skill?.name ? `/${task.skill.name}` : "", task.folderName, ACCESS_LABELS[task.access] || "", task.web ? "Web" : ""].filter(Boolean).join(" · ");
    const next = running ? "Running now" : queued ? "Starting soon"
      : task.enabled && task.nextRun ? `Next: ${moment(task.nextRun)}` : "Off";
    const notes = [];
    if (task.missed) {
      const run = el("button", { type: "button", className: "button small accent", text: "Run now", disabled: running || queued || !state.active });
      run.addEventListener("click", () => {
        run.disabled = true;
        void act("/api/schedules/run", { id: task.id }).then((ok) => { if (!ok) run.disabled = false; });
      });
      const skip = el("button", { type: "button", className: "button small", text: "Skip" });
      skip.addEventListener("click", () => void act("/api/schedules/skip", { id: task.id }));
      notes.push(el("div", { className: "task-missed" },
        el("span", { text: `Missed ${moment(task.missed)} while Pipkin was closed.` }), run, skip));
    }
    if (task.waitReason && !running) notes.push(el("p", { className: "task-note", text: `Waiting because ${task.waitReason}.` }));
    if (task.lastRun && !task.missed) {
      const last = STATUS[task.lastStatus]?.label || "Ran";
      notes.push(el("p", { className: "task-note", text: `Last run: ${last.toLowerCase()}, ${moment(task.lastRun)}` }));
    }
    const runNow = el("button", { type: "button", className: "button small", text: "Run now", disabled: running || queued || !state.active });
    runNow.addEventListener("click", () => {
      runNow.disabled = true;
      void act("/api/schedules/run", { id: task.id }).then((ok) => {
        if (ok) toast(`Starting “${task.title}”`);
        else runNow.disabled = false;
      });
    });
    const toggle = el("input", { type: "checkbox", checked: Boolean(task.enabled) });
    toggle.addEventListener("change", () => {
      void act(toggle.checked ? "/api/schedules/enable" : "/api/schedules/disable", { id: task.id }).then((ok) => {
        if (!ok) toggle.checked = !toggle.checked;
      });
    });
    const edit = el("button", { type: "button", className: "icon-button small", title: "Edit", "aria-label": `Edit “${task.title}”` }, icon("edit"));
    edit.addEventListener("click", () => openEditor(task));
    return el("article", { className: "task-row" + (task.enabled ? "" : " off") + (running ? " running" : "") },
      el("div", { className: "task-main" },
        el("div", { className: "task-title" }, el("strong", { text: task.title }), el("span", { className: "task-next", text: next })),
        el("span", { className: "task-when", text: details }),
        notes),
      el("div", { className: "task-actions" }, runNow,
        el("label", { className: "switch", title: task.enabled ? "Turn off" : "Turn on" }, toggle, el("span"),
          el("span", { className: "sr-only", text: `${task.title} on` })),
        edit));
  }

  /* ---------- the task editor ---------- */

  function renderDays(selected) {
    byId("schedule-days").replaceChildren(...DAYS.map((name, index) => {
      const button = el("button", { type: "button", className: "day-chip", text: name, "aria-pressed": String(selected.includes(index)) });
      button.addEventListener("click", () => button.setAttribute("aria-pressed", String(button.getAttribute("aria-pressed") !== "true")));
      return button;
    }));
  }

  function syncWhen() {
    const kind = byId("schedule-kind").value;
    byId("schedule-date-field").hidden = kind !== "once";
    byId("schedule-days-field").hidden = kind !== "weekly";
  }

  // Set while the editor reviews a task Pipkin proposed in a Work run: Save hands the task back instead of saving it.
  let draft = null;

  function showFolder() {
    byId("schedule-folder-pick").hidden = Boolean(draft);
    const name = draft?.folderName || editorWorkspace?.name || editing?.folderName || "";
    byId("schedule-folder-name").textContent = name || "No folder chosen";
    byId("schedule-folder-name").dataset.empty = String(!name);
    byId("schedule-folder-pick").textContent = name ? "Change…" : "Choose…";
  }

  function showError(message) {
    const box = byId("schedule-error");
    box.hidden = !message;
    box.textContent = message || "";
  }

  let editorSkills = new Map();

  function populateSkillPicker(currentSkill) {
    const select = byId("schedule-skill");
    editorSkills = new Map();
    const list = options.getSkills?.() || [];
    for (const skill of list) {
      if (skill?.name && skill.instructions) editorSkills.set(skill.name, skill);
    }
    if (currentSkill?.name && !editorSkills.has(currentSkill.name)) {
      editorSkills.set(currentSkill.name, currentSkill);
    }
    const opts = [el("option", { value: "", text: "None (custom prompt)" })];
    for (const skill of editorSkills.values()) {
      const checks = skill.checks?.length ? ` (${skill.checks.length} ${skill.checks.length === 1 ? "check" : "checks"})` : "";
      opts.push(el("option", { value: skill.name, text: `/${skill.name}${skill.description ? " — " + skill.description : ""}${checks}` }));
    }
    select.replaceChildren(...opts);
    select.value = currentSkill?.name || "";
    syncSkillNote();
  }

  function syncSkillNote() {
    const name = byId("schedule-skill").value;
    const skill = editorSkills.get(name);
    const note = byId("schedule-skill-note");
    const prompt = byId("schedule-prompt");
    if (!skill) {
      note.hidden = true;
      note.textContent = "";
      prompt.placeholder = "Turn this week's notes into a review sheet, with one section per class.";
      return;
    }
    const checkText = skill.checks?.length
      ? ` Runs ${skill.checks.length} verification ${skill.checks.length === 1 ? "check" : "checks"} automatically.`
      : "";
    note.textContent = `Runs /${skill.name} on schedule.${checkText} Add optional input or focus below.`;
    note.hidden = false;
    prompt.placeholder = `Optional input for /${skill.name} (leave blank to run as-is).`;
  }

  /* task: an existing task to edit. prefill: field values for a new task. review: {folderName, onSave} to review a
     proposed task, where Save passes the task to onSave instead of creating it here. */
  function openEditor(task, { prefill = null, review = null } = {}) {
    editing = task;
    draft = review;
    deleteArmed = false;
    const workspace = options.getWorkspace?.();
    editorWorkspace = task || review ? null : workspace ? { key: workspace.key, name: workspace.name } : null;
    const source = task || prefill;
    const today = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const schedule = source?.schedule || { kind: "weekly", time: "17:00", days: [(today.getDay() + 6) % 7] };
    byId("schedule-editor-heading").textContent = review ? "Review scheduled task" : task ? "Edit scheduled task" : "New scheduled task";
    byId("schedule-save").textContent = review ? "Create task" : "Save task";
    populateSkillPicker(source?.skill || null);
    byId("schedule-prompt").value = source?.prompt || "";
    byId("schedule-title").value = source?.title || "";
    byId("schedule-kind").value = schedule.kind;
    byId("schedule-time").value = schedule.time;
    byId("schedule-date").value = schedule.date || `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
    renderDays(schedule.days || [(today.getDay() + 6) % 7]);
    for (const radio of document.querySelectorAll('input[name="schedule-access"]')) radio.checked = radio.value === (source?.access || "read");
    byId("schedule-web").checked = Boolean(source?.web);
    byId("schedule-delete").hidden = !task;
    byId("schedule-delete").textContent = "Delete task";
    syncWhen();
    showFolder();
    showError("");
    editor().showModal();
    byId("schedule-prompt").focus();
  }

  async function saveTask(event) {
    event.preventDefault();
    const kind = byId("schedule-kind").value;
    const schedule = { kind, time: byId("schedule-time").value };
    if (kind === "once") schedule.date = byId("schedule-date").value;
    if (kind === "weekly") {
      schedule.days = [...byId("schedule-days").children].map((button, index) => button.getAttribute("aria-pressed") === "true" ? index : -1).filter((day) => day >= 0);
    }
    const chosenSkill = editorSkills.get(byId("schedule-skill").value) || null;
    const promptText = byId("schedule-prompt").value.trim();
    if (!promptText && !chosenSkill) {
      showError("Describe what the task should do, or choose a skill.");
      return;
    }
    if (!editing && !editorWorkspace && !draft) {
      showError("Choose the folder this task works in.");
      return;
    }
    const skillPayload = chosenSkill ? {
      name: chosenSkill.name,
      instructions: chosenSkill.instructions,
      ...(chosenSkill.checks?.length ? { checks: chosenSkill.checks } : {}),
      ...(chosenSkill.output ? { output: chosenSkill.output } : {}),
    } : null;
    const task = {
      title: byId("schedule-title").value.trim(), prompt: promptText, schedule,
      skill: skillPayload,
      access: document.querySelector('input[name="schedule-access"]:checked')?.value || "read",
      // Saving a task means you want it to run, so it is switched on again if it was off.
      web: byId("schedule-web").checked, enabled: true,
    };
    if (draft) {
      if (kind === "weekly" && !schedule.days.length) {
        showError("Choose at least one day of the week.");
        return;
      }
      if (kind === "once") {
        if (!schedule.date) {
          showError("Choose a date for the one-time run.");
          return;
        }
        const chosen = new Date(`${schedule.date}T${schedule.time || "00:00"}`);
        if (Number.isNaN(chosen.getTime()) || chosen.getTime() <= Date.now()) {
          showError("That time has already passed. Choose a time in the future.");
          return;
        }
      }
      const { onSave } = draft;
      draft = null;
      editor().close();
      onSave(task);
      return;
    }
    const save = byId("schedule-save");
    save.disabled = true;
    pendingActions++;
    ++requestVersion;
    try {
      const next = await api("/api/schedules/save", { ...(editing && { id: editing.id }), task, ...(editorWorkspace && { workspace: editorWorkspace.key }) });
      apply(next);
      editor().close();
      toast(editing ? "Task updated" : `Scheduled “${next.task.title}” · ${moment(next.task.nextRun)}`);
      if (dialog().open) {
        view = "tasks";
        open();
      }
    } catch (error) {
      showError(error.code === "workspace_required" ? "Pipkin lost access to that folder. Choose it again." : error.message);
      if (error.code === "workspace_required") {
        editorWorkspace = null;
        showFolder();
      }
    } finally {
      save.disabled = false;
      if (--pendingActions === 0) void refresh();
    }
  }

  async function deleteTask() {
    const button = byId("schedule-delete");
    if (!deleteArmed) {
      deleteArmed = true;
      button.textContent = "Click again to delete";
      return;
    }
    if (await act("/api/schedules/delete", { id: editing.id })) {
      editor().close();
      toast(`Deleted “${editing.title}”`);
    }
  }

  async function pickFolder() {
    const workspace = await options.chooseWorkspace?.();
    if (workspace) {
      editorWorkspace = { key: workspace.key, name: workspace.name };
      showFolder();
      showError("");
    }
  }

  /* ---------- setup ---------- */

  function init(config) {
    options = config;
    byId("open-scheduled").addEventListener("click", () => open(state.inbox.some((item) => !item.read) ? "inbox" : view));
    byId("schedule-new").addEventListener("click", () => openEditor(null));
    byId("schedule-read-all").addEventListener("click", () => void act("/api/schedules/read", { all: true }));
    byId("schedule-paused").addEventListener("change", (event) => {
      const toggle = event.target;
      void act("/api/schedules/pause", { paused: toggle.checked }).then((ok) => {
        if (!ok) toggle.checked = Boolean(state.paused);
      });
    });
    for (const radio of document.querySelectorAll('input[name="schedule-view"]')) {
      radio.addEventListener("change", () => {
        view = radio.value;
        render();
      });
    }
    byId("schedule-form").addEventListener("submit", saveTask);
    byId("schedule-cancel").addEventListener("click", () => editor().close());
    editor().addEventListener("close", () => { draft = null; });
    byId("schedule-delete").addEventListener("click", () => void deleteTask());
    byId("schedule-folder-pick").addEventListener("click", () => void pickFolder());
    byId("schedule-kind").addEventListener("change", syncWhen);
    byId("schedule-skill").addEventListener("change", syncSkillNote);
    dialog().addEventListener("close", schedulePoll);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void refresh();
    });
    void refresh();
  }

  function openDraft(task, review) {
    openEditor(null, { prefill: task, review });
  }

  async function runTask(taskId, title = "") {
    if (!taskId) return false;
    const ok = await act("/api/schedules/run", { id: taskId });
    if (ok) toast(title ? `Starting “${title}”` : "Starting scheduled task");
    return ok;
  }

  /* ---------- Chat: a proposed task, created only when the user confirms ----------
     record = { proposal: {task, nextRun, needsClarification}, state: "pending" | "creating" | "created" | "dismissed",
     confirmationId, folder?: {key, name}, taskId?, nextRun? }. The record is saved with the chat message, and the
     confirmation id makes a retried Create return the task it already made instead of a second one. */
  function renderChatCard(container, record, { onChange } = {}) {
    const proposal = record.proposal || {};
    const task = record.task || proposal.task || {};
    if (!record.folder) {
      const workspace = options.getWorkspace?.();
      if (workspace) record.folder = { key: workspace.key, name: workspace.name };
    }
    const draw = () => renderChatCard(container, record, { onChange });
    const changed = () => {
      onChange?.();
      draw();
    };
    const card = el("div", { className: "approval-card schedule-proposal chat-schedule-card",
      dataset: { decision: record.state === "created" ? "approved" : record.state === "dismissed" ? "denied" : "pending" } });
    if (record.state === "created" || record.state === "dismissed") {
      const created = record.state === "created";
      card.append(el("header", {}, icon(created ? "check" : "x"),
        el("span", { text: `${created ? "Scheduled" : "Not scheduled"} · ${record.title || task.title || "Scheduled task"}` })));
      if (created) {
        const view = el("button", { type: "button", className: "button small", text: "View in Scheduled" });
        view.addEventListener("click", () => open("tasks"));
        card.append(el("footer", {}, el("span", { className: "decision",
          text: record.nextRun ? `Next run: ${moment(record.nextRun)}` : "Saved in Scheduled." }), view));
      }
      container.replaceChildren(card);
      return;
    }
    // Same layout as the Work run's schedule card (agent-view.js), plus a folder chooser: Chat has no run folder.
    const flagged = new Set(record.task ? [] : proposal.needsClarification || []);
    const whenGuessed = ["kind", "days", "time", "date"].some((field) => flagged.has(field));
    const chip = (glyph, label, value, extra) => el("div", { className: "schedule-proposal-chip" },
      icon(glyph), el("span", { className: "chip-label", text: label }), el("strong", { text: value }), extra || null);
    const pick = el("button", { type: "button", className: "button small chat-schedule-pick", text: record.folder ? "Change…" : "Choose…" });
    pick.addEventListener("click", async () => {
      const folder = await options.chooseWorkspace?.();
      if (folder) {
        record.folder = { key: folder.key, name: folder.name };
        record.error = "";
        changed();
      }
    });
    card.append(
      el("header", {}, icon("clock"), el("span", { text: "Create this scheduled task?" }),
        el("span", { className: "schedule-proposal-perm", text: (task.access === "create" ? "Creates new files" : "Read only") + (task.web ? " · Web" : "") })),
      el("div", { className: "schedule-proposal-hero" },
        el("div", { className: "schedule-proposal-title", text: task.title || "Scheduled task" }),
        el("div", { className: "schedule-proposal-when" + (whenGuessed ? " guessed" : "") }, icon("clock"),
          el("span", { text: task.schedule ? describe(task.schedule) : "Not set" }),
          whenGuessed ? el("span", { className: "schedule-guessed-pill", text: "Guessed" }) : null)),
      el("div", { className: "schedule-proposal-meta" },
        chip("clock", "Next run", !record.task && proposal.nextRun ? moment(proposal.nextRun) : "After you create it"),
        el("div", { className: "schedule-proposal-chip chat-schedule-folder" + (record.folder ? "" : " missing") },
          icon("folder"), el("span", { className: "chip-label", text: "Folder" }),
          el("strong", { text: record.folder?.name || "Choose a folder" }), pick),
        chip("shield", "Safeguards", task.access === "create" ? "Never edits existing files or runs commands" : "Read-only · never runs commands")),
      el("div", { className: "schedule-proposal-prompt" }, el("span", { text: "What Pipkin will do each run" }), el("p", { text: task.prompt || "" })));
    if (flagged.size) {
      const names = { kind: "how often", days: "which days", time: "the time", date: "the date" };
      const list = [...flagged].map((field) => names[field]).filter(Boolean).join(", ");
      card.append(el("div", { className: "schedule-proposal-note" }, icon("alert"),
        el("span", { text: `Pipkin guessed ${list}. Double-check the schedule above, or use Edit details.` })));
    }
    if (record.error) card.append(el("div", { className: "schedule-proposal-note", role: "alert" }, icon("alert"), el("span", { text: record.error })));
    const busy = record.state === "creating" && record.busy;
    const create = async (edited) => {
      if (!record.folder) {
        record.error = "Choose the folder this task works in first.";
        draw();
        return;
      }
      if (edited) record.task = edited;
      record.state = "creating";
      record.busy = true;
      record.error = "";
      changed();   // saved before the request, so a reload retries with the same confirmation id
      try {
        const next = await api("/api/schedules/save", { task: record.task || proposal.task, workspace: record.folder.key,
          confirmationId: record.confirmationId });
        apply(next);
        Object.assign(record, { state: "created", taskId: next.task.id, title: next.task.title, nextRun: next.task.nextRun });
        toast(`Scheduled “${next.task.title}” · ${moment(next.task.nextRun)}`);
      } catch (error) {
        record.state = "pending";
        if (error.code === "workspace_required") {
          record.folder = null;
          record.error = "Pipkin lost access to that folder. Choose it again.";
        } else {
          record.error = error.message || "Pipkin couldn't create the task.";
        }
      } finally {
        delete record.busy;
        changed();
      }
    };
    const notNow = el("button", { type: "button", className: "button small", text: "Not now", disabled: busy });
    const edit = el("button", { type: "button", className: "button small", text: "Edit details…", disabled: busy });
    const confirm = el("button", { type: "button", className: "button small accent", text: busy ? "Creating…" : "Create task", disabled: busy });
    notNow.addEventListener("click", () => {
      record.state = "dismissed";
      changed();
    });
    edit.addEventListener("click", () => openDraft(record.task || proposal.task, {
      folderName: record.folder?.name || "Choose on the card", onSave: (edited) => void create(edited),
    }));
    confirm.addEventListener("click", () => void create());
    card.append(el("footer", {}, el("span", { className: "decision", text: "Runs automatically while Pipkin is open." }), notNow, edit, confirm));
    container.replaceChildren(card);
  }

  window.PipkinSchedule = { init, open, refresh, runningTitle, describe, moment, openDraft, runTask, renderChatCard };
})();
