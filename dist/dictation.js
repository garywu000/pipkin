"use strict";

/*
 * Local dictation. Hold the mic button to talk (release to finish), or click it / press the shortcut to start and
 * stop. Speech is transcribed on this device into an editable preview; nothing is inserted or sent until the user
 * chooses Insert. Recordings live only in memory and are dropped once transcribed.
 */
(() => {
  const { el, icon, toast, formatSize } = window.PipkinUI;
  const MAX_SECONDS = 120;
  const HOLD_MS = 350;
  const LANGUAGE_NAMES = {
    auto: "Detect automatically", en: "English", zh: "Chinese", es: "Spanish", fr: "French", de: "German",
    it: "Italian", pt: "Portuguese", nl: "Dutch", ru: "Russian", uk: "Ukrainian", pl: "Polish", tr: "Turkish",
    ar: "Arabic", hi: "Hindi", ja: "Japanese", ko: "Korean", vi: "Vietnamese", id: "Indonesian", th: "Thai",
    sv: "Swedish", da: "Danish", no: "Norwegian", fi: "Finnish", cs: "Czech", el: "Greek", he: "Hebrew",
    ro: "Romanian", hu: "Hungarian", ms: "Malay",
  };

  let deps = null;
  let state = "idle"; // idle | starting | recording | transcribing | preview
  let status = null;
  let capture = null;
  let pressTimer = 0;
  let heldToTalk = false;
  let stopOnRelease = false;
  let selection = null;
  let preview = null;
  let pollTimer = 0;
  let generation = 0;

  const byId = (id) => document.getElementById(id);
  const button = () => byId("dictate");
  const prefs = () => {
    const settings = deps.getSettings();
    const chosen = status?.models.find((m) => m.id === settings.dictationModel);
    // While a newly chosen model downloads, keep dictating with one that's already installed.
    const model = chosen?.installed ? chosen.id : status?.models.find((m) => m.installed)?.id || chosen?.id || "base";
    return {
      model,
      language: LANGUAGE_NAMES[settings.dictationLanguage] ? settings.dictationLanguage : "auto",
      // Simple mode (the default) puts the words straight into the message box; Advanced adds the preview and tidy-up.
      advanced: settings.dictationAdvanced === true,
      tidy: settings.dictationAdvanced === true && settings.dictationTidy === true,
    };
  };

  async function post(path, body = {}, signal) {
    const response = await fetch("/api/dictation/" + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || "Dictation isn't available right now.");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function refreshStatus() {
    try {
      status = await post("status");
    } catch {
      status = null;
    }
    renderSettings();
    syncButton();
    return status;
  }

  function ready() {
    const model = status?.models.find((m) => m.id === prefs().model);
    return Boolean(status?.engine && model?.installed);
  }

  /* ---------- Mic button ---------- */
  function syncButton() {
    const mic = button();
    if (!mic) return;
    const recording = state === "recording" || state === "starting";
    mic.classList.toggle("recording", recording);
    mic.classList.toggle("transcribing", state === "transcribing");
    mic.setAttribute("aria-pressed", String(recording));
    mic.dataset.state = state;
    mic.disabled = state === "transcribing" || deps.promptBox.readOnly || deps.promptBox.disabled;
    const label = recording ? "Stop dictating" : "Dictate";
    mic.setAttribute("aria-label", label);
    const tip = byId("dictate-tip");
    if (tip) {
      tip.querySelector(".tip-label").textContent = recording ? (heldToTalk ? "Release to finish" : "Click to finish") : "Dictate";
      tip.querySelector(".tip-detail").textContent = recording ? "Esc cancels" : "Hold to talk · right-click for options";
      const keys = tip.querySelector(".tip-keys");
      const parts = deps.shortcutParts?.() || [];
      if (keys.dataset.combo !== parts.join(" ")) {
        keys.dataset.combo = parts.join(" ");
        keys.replaceChildren(...parts.map((part) => el("kbd", { text: part })));
      }
      keys.hidden = !parts.length;
    }
  }

  function onPointerDown(event) {
    if (event.button !== 0 || button().disabled) return;
    event.preventDefault();
    if (state === "recording" || state === "starting") {
      stopOnRelease = true; // a second click finishes a click-started recording
      return;
    }
    if (state === "preview") discardPreview();
    heldToTalk = stopOnRelease = false;
    if (!ready()) {
      setupMenu();
      return;
    }
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => {
      if (state === "recording" || state === "starting") {
        heldToTalk = true;
        syncButton();
        renderBar();
      }
    }, HOLD_MS);
    void start();
  }

  function onPointerUp(event) {
    if (event.button !== 0) return;
    clearTimeout(pressTimer);
    if (heldToTalk) {
      heldToTalk = false;
      void finish();
    } else if (stopOnRelease) {
      stopOnRelease = false;
      void finish();
    }
    // Otherwise it was a quick click: keep listening until the next click, Done, the shortcut or Esc.
  }

  function onKeyClick(event) {
    // Enter / Space on the focused button (pointer clicks are handled by pointerdown / pointerup).
    if (event.detail !== 0) return;
    toggle();
  }

  function toggle() {
    if (state === "recording" || state === "starting") {
      void finish();
    } else if (state === "idle" || state === "preview") {
      if (state === "preview") discardPreview();
      if (!ready()) {
        setupMenu();
        return;
      }
      heldToTalk = stopOnRelease = false;
      void start();
    }
  }

  function setupMenu() {
    if (!status) {
      void refreshStatus().then(() => (status ? (ready() ? toggle() : setupMenu()) : deps.announce("Dictation isn't available right now.", true)));
      return;
    }
    if (!status.engine) {
      deps.announce("This build of Pipkin doesn't include the speech engine.", true);
      return;
    }
    optionsMenu(true);
  }

  function optionsMenu(needsModel = false) {
    if (!status) {
      void refreshStatus().then(() => status && optionsMenu(needsModel));
      return;
    }
    const current = prefs();
    const items = status.models.map((model) => ({
      icon: model.installed ? "speak" : "download",
      label: model.label,
      detail: model.installed ? model.detail : `Download · ${formatSize(model.size)}`,
      checked: model.installed && model.id === current.model,
      onSelect: () => {
        deps.saveSettings({ dictationModel: model.id });
        if (!model.installed) void download(model.id);
        renderSettings();
      },
    }));
    items.push({ icon: "globe", label: "Language", detail: LANGUAGE_NAMES[current.language], onSelect: () => deps.openSettings("tab-voice") });
    items.push({ icon: "sparkle", label: "Advanced mode", detail: current.advanced ? "Preview and tidy before inserting" : "Off · words go straight into the box",
      checked: current.advanced, onSelect: () => { deps.saveSettings({ dictationAdvanced: !current.advanced }); renderSettings(); } });
    items.push({ icon: "settings", label: "Voice settings…", detail: needsModel ? "Download a speech model to start" : "Models, language and tidy-up",
      onSelect: () => deps.openSettings("tab-voice") });
    deps.openPopover(button(), items);
  }

  /* ---------- Recording ---------- */
  async function start() {
    if (state !== "idle") return;
    const run = ++generation;
    selection = { start: deps.promptBox.selectionStart, end: deps.promptBox.selectionEnd };
    state = "starting";
    syncButton();
    renderBar();
    let stream = null;
    let context = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error(), { name: "NotSupportedError" });
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (run !== generation) throw Object.assign(new Error(), { name: "AbortError" });
      context = new AudioContext();
      if (context.state === "suspended") await context.resume();
      await context.audioWorklet.addModule("/dictation-worklet.js");
      if (run !== generation) throw Object.assign(new Error(), { name: "AbortError" });
      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, "pipkin-capture");
      const mute = context.createGain();
      mute.gain.value = 0;
      source.connect(node).connect(mute).connect(context.destination);
      capture = { stream, context, node, chunks: [], samples: 0, peak: 0, levels: [], startedAt: performance.now(),
        timer: 0, flushResolve: null };
      node.port.onmessage = ({ data }) => {
        if (!capture || capture.node !== node) return;
        if (data?.type === "flushed") {
          capture.flushResolve?.();
          capture.flushResolve = null;
          return;
        }
        capture.chunks.push(data);
        capture.samples += data.length;
        let peak = 0;
        for (let i = 0; i < data.length; i += 4) peak = Math.max(peak, Math.abs(data[i]));
        capture.peak = Math.max(capture.peak, peak);
        capture.levels.push(peak);
        if (capture.levels.length > 28) capture.levels.shift();
        drawLevels();
        if (capture.samples / context.sampleRate >= MAX_SECONDS) {
          toast("Dictation stops at 2 minutes. Transcribing what you said.");
          void finish();
        }
      };
      for (const track of stream.getAudioTracks()) {
        track.addEventListener("ended", () => { if (capture?.stream === stream) void finish(); });
      }
      capture.timer = setInterval(renderTime, 250);
      state = "recording";
      syncButton();
      renderBar();
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      void context?.close().catch(() => {});
      if (run !== generation) return;
      state = "idle";
      capture = null;
      syncButton();
      renderBar();
      if (error?.name !== "AbortError") deps.announce(micProblem(error), true);
    }
  }

  function micProblem(error) {
    const where = deps.isMac ? "System Settings › Privacy & Security › Microphone" : "Settings › Privacy & security › Microphone";
    if (error?.name === "NotAllowedError" || error?.name === "SecurityError") return `Pipkin can't use the microphone. Allow Pipkin in ${where}, then try again.`;
    if (error?.name === "NotFoundError" || error?.name === "OverconstrainedError") return "No microphone found. Connect one and try again.";
    if (error?.name === "NotReadableError") return "The microphone is busy in another app. Close it there and try again.";
    return "Couldn't start the microphone.";
  }

  function flushCapture() {
    if (!capture) return Promise.resolve();
    const active = capture;
    return new Promise((resolve) => {
      const done = () => { clearTimeout(timer); resolve(); };
      const timer = setTimeout(done, 70);
      active.flushResolve = done;
      try { active.node.port.postMessage({ type: "flush" }); } catch { done(); }
    });
  }

  function release() {
    if (!capture) return null;
    const taken = capture;
    capture = null;
    clearInterval(taken.timer);
    taken.flushResolve = null;
    taken.node.port.onmessage = null;
    taken.stream.getTracks().forEach((track) => track.stop());
    void taken.context.close().catch(() => {});
    return taken;
  }

  function cancel() {
    generation += 1;
    heldToTalk = stopOnRelease = false;
    clearTimeout(pressTimer);
    release();
    if (state === "recording" || state === "starting" || state === "transcribing") state = "idle";
    transcribeController?.abort();
    syncButton();
    renderBar();
  }

  let transcribeController = null;
  async function finish() {
    if (state === "starting") {
      cancel();
      return;
    }
    if (state !== "recording") return;
    const run = ++generation;
    // Flush the worklet's partial tail buffer so the end of the last word isn't clipped.
    await flushCapture();
    if (run !== generation) return;
    const taken = release();
    if (!taken) return;
    state = "transcribing";
    syncButton();
    renderBar();
    try {
      const seconds = taken.samples / taken.context.sampleRate;
      if (seconds < 0.35) throw new Error("Hold the mic a little longer, or click it once to start and again to stop.");
      if (taken.peak < 0.012) throw new Error("Didn't hear anything. Check that the right microphone is selected and not muted.");
      const pcm = await toPcm16(taken.chunks, taken.context.sampleRate);
      taken.chunks.length = 0;
      transcribeController = new AbortController();
      const { model, language, tidy, advanced } = prefs();
      const data = await post("transcribe", { model, language, audio: base64(pcm) }, transcribeController.signal);
      if (run !== generation) return;
      if (!data.text) throw new Error("Didn't catch any words. Try again a little closer to the microphone.");
      if (!advanced) {
        state = "idle";
        renderBar();
        placeText(data.text);
        syncButton();
        return;
      }
      const modelLabel = status?.models.find((m) => m.id === model)?.label || model;
      showPreview(data.text, { seconds, modelLabel });
      if (tidy) void tidyUp();
    } catch (error) {
      if (run !== generation || error?.name === "AbortError") return;
      state = "idle";
      syncButton();
      renderBar();
      deps.announce(error.message || "Couldn't transcribe that.", true);
    } finally {
      transcribeController = null;
    }
  }

  /* Resample to 16 kHz mono with the browser's own resampler, trim long leading/trailing silence (keeping 200 ms padding),
     and convert to signed 16-bit PCM. Trimming silence speeds up whisper.cpp and reduces phantom silence hallucinations. */
  async function toPcm16(chunks, rate) {
    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const length = Math.max(1, Math.round(total * 16000 / rate));
    const offline = new OfflineAudioContext(1, length, 16000);
    const buffer = offline.createBuffer(1, total, rate);
    const channel = buffer.getChannelData(0);
    let offset = 0;
    for (const chunk of chunks) {
      channel.set(chunk, offset);
      offset += chunk.length;
    }
    const source = offline.createBufferSource();
    source.buffer = buffer;
    source.connect(offline.destination);
    source.start();
    const rendered = (await offline.startRendering()).getChannelData(0);
    const threshold = 0.008;
    const pad = 3200; // 200 ms at 16 kHz
    let first = 0;
    while (first < rendered.length && Math.abs(rendered[first]) < threshold) first += 1;
    let last = rendered.length - 1;
    while (last > first && Math.abs(rendered[last]) < threshold) last -= 1;
    const startIdx = first < last ? Math.max(0, first - pad) : 0;
    const endIdx = first < last ? Math.min(rendered.length, last + pad + 1) : rendered.length;
    const count = Math.max(1, endIdx - startIdx);
    const pcm = new Int16Array(count);
    for (let i = 0; i < count; i += 1) {
      const sample = Math.max(-1, Math.min(1, rendered[startIdx + i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return new Uint8Array(pcm.buffer);
  }

  function base64(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  /* ---------- Recording bar ---------- */
  function renderBar() {
    const bar = byId("dictation-bar");
    if (!bar) return;
    const active = state === "starting" || state === "recording" || state === "transcribing";
    bar.hidden = !active;
    bar.dataset.state = state;
    if (!active) return;
    byId("dictation-label").textContent = state === "transcribing" ? "Transcribing on this device…"
      : state === "starting" ? "Starting the microphone…" : heldToTalk ? "Listening · release to finish" : "Listening";
    byId("dictation-done").hidden = state === "transcribing" || heldToTalk;
    renderTime();
    drawLevels();
  }

  function renderTime() {
    const time = byId("dictation-time");
    if (!time) return;
    const seconds = capture ? Math.floor(capture.samples / capture.context.sampleRate) : 0;
    time.textContent = state === "recording" ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "";
  }

  function drawLevels() {
    const meter = byId("dictation-levels");
    if (!meter) return;
    const levels = capture?.levels || [];
    if (meter.childElementCount !== 28) meter.replaceChildren(...Array.from({ length: 28 }, () => el("span")));
    [...meter.children].forEach((bar, index) => {
      const level = levels[levels.length - 28 + index] || 0;
      bar.style.transform = `scaleY(${Math.max(0.12, Math.min(1, Math.sqrt(level) * 1.6)).toFixed(3)})`;
    });
  }

  /* ---------- Preview ---------- */
  function showPreview(text, meta = {}) {
    state = "preview";
    preview = {
      raw: text, tidied: null, tidiedSource: null, view: "raw",
      edited: { raw: text, tidied: null }, tidying: false, note: "",
      seconds: meta.seconds || 0, modelLabel: meta.modelLabel || "",
    };
    syncButton();
    renderBar();
    renderPreview();
    byId("dictation-text")?.focus();
  }

  function renderPreview() {
    const box = byId("dictation-preview");
    if (!box) return;
    box.hidden = !preview;
    if (!preview) return;
    const text = byId("dictation-text");
    const value = preview.edited[preview.view] ?? "";
    if (text.value !== value) text.value = value;
    text.style.height = "auto";
    text.style.height = Math.min(text.scrollHeight, 180) + "px";
    const metaEl = byId("dictation-preview-meta");
    if (metaEl) {
      const parts = ["Dictation"];
      if (preview.seconds >= 0.5) parts.push(`${Math.max(1, Math.round(preview.seconds))}s`);
      if (preview.modelLabel) parts.push(preview.modelLabel);
      metaEl.textContent = parts.join(" · ");
    }
    for (const option of box.querySelectorAll("[data-view]")) {
      const selected = option.dataset.view === preview.view;
      option.setAttribute("aria-checked", String(selected));
      option.classList.toggle("active", selected);
    }
    const tidyButton = box.querySelector('[data-view="tidied"]');
    tidyButton.disabled = preview.tidying;
    tidyButton.textContent = preview.tidying ? "Tidying…" : "Tidied";
    byId("dictation-note").textContent = preview.note;
    byId("dictation-insert").disabled = !value.trim();
  }

  async function tidyUp() {
    if (!preview || preview.tidying) return;
    const current = preview;
    const sourceText = current.edited.raw;
    current.tidying = true;
    current.note = "Your local model is tidying punctuation and filler words…";
    renderPreview();
    try {
      const data = await post("clean", { text: sourceText });
      if (preview !== current) return;
      current.tidied = data.text;
      current.tidiedSource = sourceText;
      current.edited.tidied = data.text;
      current.view = "tidied";
      current.note = data.kept ? "Kept your exact words: tidying would have changed a name, number or detail." : "Tidied by your local model. Check it before inserting.";
    } catch (error) {
      if (preview !== current) return;
      current.view = "raw";
      current.note = error.message || "Couldn't tidy this up. The original text is unchanged.";
    } finally {
      current.tidying = false;
      if (preview === current) renderPreview();
    }
  }

  function choose(view) {
    if (!preview || preview.view === view) return;
    if (view === "tidied" && (preview.edited.tidied === null || preview.tidiedSource !== preview.edited.raw)) {
      void tidyUp();
      return;
    }
    preview.view = view;
    preview.note = "";
    renderPreview();
  }

  function insert() {
    if (!preview) return;
    const text = (preview.edited[preview.view] || "").trim();
    if (!text) return;
    placeText(text);
    preview = null;
    state = "idle";
    renderPreview();
    syncButton();
  }

  /* Put text into the message box where the cursor was when dictation started. Never sends. */
  function placeText(text) {
    const box = deps.promptBox;
    const start = Math.min(selection?.start ?? box.value.length, box.value.length);
    const end = Math.min(Math.max(selection?.end ?? start, start), box.value.length);
    const before = box.value.slice(0, start);
    const after = box.value.slice(end);
    const insertText = (before && !/\s$/.test(before) ? " " : "") + text + (after && !/^\s/.test(after) ? " " : "");
    box.focus();
    box.setSelectionRange(start, end);
    // execCommand keeps the insertion on the native undo stack (⌘Z / Ctrl+Z undoes it).
    const inserted = document.execCommand?.("insertText", false, insertText);
    if (!inserted) {
      box.setRangeText(insertText, start, end, "end");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    }
    selection = { start: box.selectionStart, end: box.selectionEnd };
    deps.onInserted();
  }

  function discardPreview() {
    preview = null;
    if (state === "preview") state = "idle";
    renderPreview();
    syncButton();
  }

  /* ---------- Settings › Voice ---------- */
  async function download(id) {
    try {
      status = await post("download", { model: id });
      renderSettings();
      schedulePoll();
      toast("Downloading the speech model. You can keep working.");
    } catch (error) {
      deps.announce(error.message, true);
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!status?.download || status.download.state !== "downloading") return;
    pollTimer = setTimeout(async () => {
      const before = status?.download?.model;
      await refreshStatus();
      if (before && !status?.download && status?.models.find((m) => m.id === before)?.installed) {
        toast(`${status.models.find((m) => m.id === before).label} speech model is ready.`);
      }
      schedulePoll();
    }, 700);
  }

  function renderSettings() {
    const panel = byId("voice-settings");
    if (!panel) return;
    const current = deps ? prefs() : { model: "base", language: "auto", tidy: false };
    const engine = byId("voice-engine-note");
    const installedBytes = (status?.models || []).filter((m) => m.installed).reduce((sum, m) => sum + m.size, 0);
    const storageNote = installedBytes ? ` (${formatSize(installedBytes)} on disk)` : "";
    engine.textContent = !status ? "Checking the speech engine…" : status.engine
      ? `Speech is turned into text on this device with whisper.cpp. Audio is never uploaded and is deleted right after it's transcribed.${storageNote}`
      : "This build of Pipkin doesn't include the speech engine. Install a release build to dictate.";
    const list = byId("voice-models");
    list.replaceChildren(...(status?.models || []).map((model) => {
      const job = status.download?.model === model.id ? status.download : null;
      const selected = model.id === current.model;
      const actions = el("div", { className: "voice-model-actions" });
      if (job?.state === "downloading") {
        const percent = Math.floor((job.received / job.size) * 100);
        actions.append(el("span", { className: "voice-progress", text: `${percent}%` },
          el("span", { className: "voice-progress-bar", style: `--progress:${percent}%` })),
        el("button", { type: "button", className: "button small", text: "Cancel", onclick: async () => { status = await post("cancel"); renderSettings(); } }));
      } else if (model.installed) {
        actions.append(
          selected ? el("span", { className: "task-status-pill", dataset: { status: "active" }, text: "In use" })
            : el("button", { type: "button", className: "button small", text: "Use", onclick: () => { deps.saveSettings({ dictationModel: model.id }); renderSettings(); syncButton(); } }),
          el("button", { type: "button", className: "button small", text: "Remove", title: `Delete the ${model.label} model (${formatSize(model.size)})`,
            onclick: async () => {
              try { status = await post("remove", { model: model.id }); } catch (error) { deps.announce(error.message, true); }
              renderSettings();
              syncButton();
            } }));
      } else {
        const retry = job?.state === "failed";
        const canDownload = Boolean(status.engine && status.download?.state !== "downloading");
        actions.append(el("button", { type: "button", className: "button small" + (selected && canDownload ? " accent" : ""), disabled: !canDownload,
          text: `${retry ? "Retry" : "Download"} · ${formatSize(model.size)}`, onclick: () => { deps.saveSettings({ dictationModel: model.id }); void download(model.id); } }));
      }
      const failed = job?.state === "failed" ? el("p", { className: "voice-error", text: job.error }) : null;
      return el("div", { className: "voice-model" + (selected ? " selected" : "") },
        el("div", { className: "voice-model-text" }, el("strong", { text: model.label }), el("p", { text: model.detail }), failed), actions);
    }));
    const language = byId("voice-language");
    if (!language.options.length) {
      for (const [code, name] of Object.entries(LANGUAGE_NAMES)) language.append(el("option", { value: code, text: name }));
    }
    language.value = current.language;
    const englishOnly = status?.models.find((m) => m.id === current.model)?.englishOnly;
    language.disabled = Boolean(englishOnly);
    byId("voice-language-note").textContent = englishOnly ? "Fast English always transcribes English." : "Pick your language if detection guesses wrong.";
    byId("voice-advanced").checked = current.advanced;
    byId("voice-tidy").checked = deps.getSettings().dictationTidy === true;
    byId("voice-tidy-row").hidden = !current.advanced;
    byId("voice-mode-hint").textContent = current.advanced
      ? "Advanced: each transcript opens in a preview you can edit, tidy, and then Insert. Dictation never sends anything."
      : "Simple: your words go straight into the message box when you finish talking. Review and send as usual — dictation never sends anything.";
  }

  /* ---------- Setup ---------- */
  function init(options) {
    deps = options;
    const mic = button();
    if (!mic) return;
    mic.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("pointerup", onPointerUp);
    document.addEventListener("pointercancel", () => {
      clearTimeout(pressTimer);
      if (heldToTalk) { heldToTalk = false; void finish(); }
    });
    mic.addEventListener("click", onKeyClick);
    mic.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      if (state === "idle" || state === "preview") optionsMenu();
    });
    byId("dictation-cancel").addEventListener("click", cancel);
    byId("dictation-done").addEventListener("click", () => void finish());
    const insertBtn = byId("dictation-insert");
    insertBtn.title = `Insert into the message (${deps.isMac ? "⌘ Enter" : "Ctrl+Enter"})`;
    insertBtn.addEventListener("click", insert);
    byId("dictation-discard").addEventListener("click", discardPreview);
    byId("dictation-rerecord")?.addEventListener("click", () => {
      discardPreview();
      if (ready()) { heldToTalk = stopOnRelease = false; void start(); }
      else setupMenu();
    });
    for (const option of document.querySelectorAll("#dictation-preview [data-view]")) {
      option.addEventListener("click", () => choose(option.dataset.view));
    }
    byId("dictation-text").addEventListener("input", (event) => {
      if (!preview) return;
      preview.edited[preview.view] = event.target.value;
      renderPreview();
    });
    byId("dictation-text").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        insert();
      }
    });
    byId("voice-language").addEventListener("change", (event) => deps.saveSettings({ dictationLanguage: event.target.value }));
    byId("voice-tidy").addEventListener("change", (event) => deps.saveSettings({ dictationTidy: event.target.checked }));
    byId("voice-advanced").addEventListener("change", (event) => {
      deps.saveSettings({ dictationAdvanced: event.target.checked });
      if (!event.target.checked && state === "preview") discardPreview();
      renderSettings();
    });
    // Escape cancels a recording (or closes the preview) before Pipkin's own Escape handling sees it.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
      if (state === "recording" || state === "starting" || state === "transcribing") {
        event.preventDefault();
        event.stopImmediatePropagation();
        cancel();
      } else if (state === "preview" && event.target.closest?.("#dictation-preview")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        discardPreview();
        deps.promptBox.focus();
      }
    }, true);
    window.addEventListener("pagehide", cancel);
    syncButton();
    void refreshStatus().then(schedulePoll);
  }

  window.PipkinDictation = {
    init,
    toggle: () => deps && toggle(),
    refresh: () => deps && refreshStatus().then(schedulePoll),
    sync: () => deps && syncButton(),
    get active() { return state !== "idle"; },
  };
})();
