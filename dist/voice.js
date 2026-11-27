"use strict";

/*
 * Conversation mode (beta): talk with Pipkin hands-free.
 * Listen → notice the end of speech → transcribe on this device (the dictation engine) → a normal Chat turn →
 * speak the reply with the computer's own on-device voices → listen again.
 * Half-duplex: microphone input is ignored while Pipkin thinks or speaks, so it never hears itself.
 * Chat only: no Agent work, no connector writes, no spoken approvals. Every turn is saved as a normal chat.
 */
(() => {
  const { el, icon } = window.PipkinUI;
  const PRE_ROLL = 8;           // ~0.34 s of audio kept from before speech starts
  const START_CHUNKS = 3;       // ~0.13 s above the threshold counts as speech
  const END_SILENCE_MS = 900;   // this much quiet ends the turn
  const MAX_TURN_SECONDS = 30;

  let deps = null;
  let session = null;
  const byId = (id) => document.getElementById(id);

  const settings = () => {
    const s = deps.getSettings();
    return { enabled: s.voiceConversation === true, voice: s.voiceName || "", rate: Number(s.voiceRate) || 1,
      volume: Number.isFinite(Number(s.voiceVolume)) ? Math.min(1, Math.max(0, Number(s.voiceVolume))) : 0.8,
      speaker: s.voiceSpeaker !== false };
  };

  /* ---------- Voices: on-device only ---------- */
  function localVoices() {
    if (!("speechSynthesis" in window)) return [];
    return speechSynthesis.getVoices().filter((voice) => voice.localService);
  }

  /* Premium / Enhanced / Natural system voices sound far more human than the compact defaults. */
  function quality(voice) {
    const label = `${voice.name} ${voice.voiceURI}`.toLowerCase();
    if (/premium|natural|neural/.test(label)) return 3;
    if (/enhanced/.test(label)) return 2;
    if (/compact|eloquence|novelty|bells|bubbles|cellos|whisper|zarvox|trinoids|organ|bad news|good news|jester|superstar|wobble|boing|bahh|albert|fred|junior|ralph|kathy|grandma|grandpa|rocko|shelley|flo|reed|sandy/.test(label)) return -1;
    return 1;
  }

  function chosenVoice() {
    const voices = localVoices();
    const wanted = settings().voice;
    const lang = (navigator.language || "en").slice(0, 2);
    const ranked = voices.filter((v) => v.lang.startsWith(lang)).sort((a, b) => quality(b) - quality(a) || (b.default - a.default));
    return voices.find((v) => v.voiceURI === wanted) || ranked[0] || voices[0] || null;
  }

  function voiceLabel(voice) {
    const q = quality(voice);
    return `${voice.name}${q >= 2 && !/premium|enhanced|natural/i.test(voice.name) ? " (high quality)" : ""} · ${voice.lang}`;
  }

  /* Turn Markdown into words worth saying. Code and links stay on screen. */
  function speakable(markdown) {
    return String(markdown || "")
      .replace(/```[\s\S]*?(```|$)/g, " (the code is on screen) ")
      .replace(/<think>[\s\S]*?(<\/think>|$)/g, " ")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/https?:\/\/\S+/g, " a link ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^\s*\|.*\|\s*$/gm, " ")
      .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
      .replace(/[*_~#]+/g, "")
      .replace(/\s+/g, " ");
  }

  /* ---------- Session ---------- */
  async function open() {
    if (session) return;
    if (!("speechSynthesis" in window) || !localVoices().length) {
      // Voices load asynchronously the first time.
      await new Promise((resolve) => {
        if (!("speechSynthesis" in window)) return resolve();
        const done = () => { speechSynthesis.removeEventListener("voiceschanged", done); resolve(); };
        speechSynthesis.addEventListener("voiceschanged", done);
        setTimeout(done, 800);
      });
    }
    await window.PipkinDictation.ensureStatus();
    if (!window.PipkinDictation.ready()) {
      deps.announce("Conversation mode uses your dictation model. Download one in Settings › Voice first.", true);
      deps.openSettings("tab-voice");
      return;
    }
    session = { gen: 0, phase: "starting", muted: false, you: "", pipkin: "", spoken: 0, queue: 0, replyEnded: false,
      capture: null, controller: null, startedAt: performance.now(), saidUpTo: 0, energy: 0, micLevel: 0 };
    deps.composer.dataset.voice = "on";
    byId("voice-overlay").hidden = false;
    byId("voice-speed").value = String(settings().rate);
    startVisuals();
    render();
    try {
      await startCapture();
      listen();
    } catch (error) {
      close();
      deps.announce(micProblem(error), true);
    }
  }

  function close() {
    if (!session) return;
    const current = session;
    session = null;
    current.gen += 1;
    current.controller?.abort();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (current.capture) {
      current.capture.node.port.onmessage = null;
      current.capture.stream.getTracks().forEach((track) => track.stop());
      void current.capture.context.close().catch(() => {});
    }
    delete deps.composer.dataset.voice;
    byId("voice-overlay").hidden = true;
    stopVisuals();
  }

  function micProblem(error) {
    const where = deps.isMac ? "System Settings › Privacy & Security › Microphone" : "Settings › Privacy & security › Microphone";
    if (error?.name === "NotAllowedError" || error?.name === "SecurityError") return `Pipkin can't use the microphone. Allow Pipkin in ${where}.`;
    if (error?.name === "NotFoundError") return "No microphone found.";
    return "Couldn't start the microphone.";
  }

  async function startCapture() {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const context = new AudioContext();
    if (context.state === "suspended") await context.resume();
    await context.audioWorklet.addModule("/dictation-worklet.js");
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, "pipkin-capture");
    const mute = context.createGain();
    mute.gain.value = 0;
    source.connect(node).connect(mute).connect(context.destination);
    session.capture = { stream, context, node, floor: 0.004, recent: [], utterance: null, loud: 0, quietMs: 0 };
    node.port.onmessage = ({ data }) => { if (data instanceof Float32Array) onAudio(data); };
    for (const track of stream.getAudioTracks()) track.addEventListener("ended", () => { close(); deps.announce("The microphone was disconnected.", true); });
  }

  /* Voice activity detection on ~42 ms chunks. */
  function onAudio(chunk) {
    const s = session;
    if (!s?.capture) return;
    const cap = s.capture;
    let sum = 0;
    for (let i = 0; i < chunk.length; i += 2) sum += chunk[i] * chunk[i];
    const rms = Math.sqrt(sum / (chunk.length / 2));
    const listening = (s.phase === "listening" || s.phase === "hearing") && !s.muted;
    s.micLevel = listening ? rms : 0;
    if (!listening) {
      cap.recent.length = 0;
      return;
    }
    const chunkMs = (chunk.length / cap.context.sampleRate) * 1000;
    const startAt = Math.max(0.012, cap.floor * 3.5);
    const keepAt = Math.max(0.008, cap.floor * 2);
    if (!cap.utterance) {
      if (rms < startAt) cap.floor = Math.max(0.002, cap.floor * 0.95 + rms * 0.05);
      cap.recent.push(chunk);
      if (cap.recent.length > PRE_ROLL) cap.recent.shift();
      cap.loud = rms >= startAt ? cap.loud + 1 : 0;
      if (cap.loud >= START_CHUNKS) {
        cap.utterance = { chunks: [...cap.recent], samples: cap.recent.reduce((n, c) => n + c.length, 0) };
        cap.recent = [];
        cap.quietMs = 0;
        s.phase = "hearing";
        render();
      }
      return;
    }
    cap.utterance.chunks.push(chunk);
    cap.utterance.samples += chunk.length;
    cap.quietMs = rms < keepAt ? cap.quietMs + chunkMs : 0;
    if (cap.quietMs >= END_SILENCE_MS || cap.utterance.samples / cap.context.sampleRate >= MAX_TURN_SECONDS) {
      const utterance = cap.utterance;
      cap.utterance = null;
      cap.loud = 0;
      void heard(utterance);
    }
  }

  function listen() {
    if (!session) return;
    session.phase = "listening";
    if (session.capture) {
      session.capture.utterance = null;
      session.capture.recent = [];
      session.capture.loud = 0;
    }
    render();
  }

  async function heard(utterance) {
    const s = session;
    const gen = ++s.gen;
    s.phase = "transcribing";
    render();
    s.controller = new AbortController();
    let text = "";
    try {
      text = (await window.PipkinDictation.transcribe(utterance.chunks, s.capture.context.sampleRate, s.controller.signal)).trim();
    } catch (error) {
      if (session !== s || gen !== s.gen || error?.name === "AbortError") return;
      s.pipkin = error.message || "Couldn't transcribe that.";
      listen();
      return;
    }
    if (session !== s || gen !== s.gen) return;
    if (text.replace(/[^\p{L}\p{N}]/gu, "").length < 2) {
      listen();
      return;
    }
    s.you = text;
    s.pipkin = "";
    s.spoken = 0;
    s.saidUpTo = 0;
    s.replyEnded = false;
    s.phase = "thinking";
    render();
    const observer = {
      delta: (answer) => { if (session === s && gen === s.gen) onReply(answer, false); },
      end: (answer, failed) => {
        if (session !== s || gen !== s.gen || s.replyEnded) return;
        s.replyEnded = true;
        if (answer === null || (failed && !answer)) {
          s.pipkin = s.pipkin || "That reply didn't finish. Try again, or type instead.";
          render();
          maybeListen(gen);
          return;
        }
        onReply(answer, true);
      },
    };
    try {
      deps.send(text, observer);
    } catch (error) {
      s.pipkin = error.message;
      s.replyEnded = true;
      render();
      setTimeout(() => { if (session === s && gen === s.gen) listen(); }, 1200);
    }
  }

  /* Speak complete sentences as they stream in, then the rest when the reply ends. */
  function onReply(answer, finished) {
    const s = session;
    const plain = speakable(answer);
    s.pipkin = plain;  // not trimmed: caption highlighting uses positions in this exact text
    let upTo = plain.length;
    if (!finished) {
      const boundary = /[.!?。！？](?=\s)|\n/g;
      let last = -1;
      for (const match of plain.matchAll(boundary)) last = match.index + match[0].length;
      upTo = last;
    }
    if (upTo > s.spoken) {
      say(plain.slice(s.spoken, upTo), s.gen);
      s.spoken = upTo;
    }
    if (finished) maybeListen(s.gen);
    render();
  }

  function say(text, gen) {
    const s = session;
    const voice = chosenVoice();
    const { rate, volume, speaker } = settings();
    const start = s.spoken;
    if (!text.trim()) return;
    if (!voice || !speaker || volume === 0) {
      // Captions only: show the sentence as "said" right away.
      s.saidUpTo = start + text.length;
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.rate = Math.min(1.6, Math.max(0.7, rate));
    utterance.volume = volume;
    s.queue += 1;
    s.phase = "speaking";
    utterance.onboundary = (event) => {
      if (session !== s || gen !== s.gen) return;
      s.saidUpTo = start + event.charIndex + (event.charLength || 1);
      s.energy = 1;  // each spoken word pulses the orb
      renderCaption();
    };
    const finish = () => {
      if (session !== s || gen !== s.gen) return;
      s.saidUpTo = Math.max(s.saidUpTo, start + text.length);
      s.queue = Math.max(0, s.queue - 1);
      renderCaption();
      maybeListen(gen);
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    speechSynthesis.speak(utterance);
  }

  function maybeListen(gen) {
    const s = session;
    if (!s || gen !== s.gen || !s.replyEnded || s.queue > 0) return;
    // A short pause so the room settles before listening again.
    setTimeout(() => { if (session === s && gen === s.gen && s.queue === 0) listen(); }, 350);
  }

  /* Interrupt: stop speaking and stop the reply, then listen. The chat keeps whatever was written. */
  function interrupt() {
    const s = session;
    if (!s || !["thinking", "speaking", "transcribing"].includes(s.phase)) return;
    s.gen += 1;
    s.controller?.abort();
    speechSynthesis.cancel();
    s.queue = 0;
    if (!s.replyEnded) deps.stopReply();
    s.replyEnded = true;
    listen();
  }

  function toggleMute() {
    if (!session) return;
    session.muted = !session.muted;
    if (session.muted && session.capture) session.capture.utterance = null;
    render();
  }

  /* ---------- UI ---------- */
  const PHASES = {
    starting: "Starting the microphone…", listening: "Listening. Ask anything.", hearing: "Listening…",
    transcribing: "Got it…", thinking: "Thinking…", speaking: "",
  };

  function render() {
    const s = session;
    if (!s) return;
    const overlay = byId("voice-overlay");
    overlay.dataset.phase = s.muted ? "muted" : s.phase;
    const phaseText = s.muted ? "Microphone paused. Click the mic to keep talking." : PHASES[s.phase] || "";
    byId("voice-phase").textContent = phaseText;
    byId("voice-phase").hidden = !phaseText;
    byId("voice-you").textContent = s.you;
    byId("voice-you").hidden = !s.you;
    const mute = byId("voice-mute");
    mute.setAttribute("aria-pressed", String(s.muted));
    mute.title = s.muted ? "Resume listening" : "Pause the microphone";
    const speakerOn = settings().speaker;
    const speakerButton = byId("voice-speaker-toggle");
    speakerButton.setAttribute("aria-pressed", String(speakerOn));
    speakerButton.title = speakerOn ? "Turn Pipkin's voice off (captions only)" : "Turn Pipkin's voice on";
    byId("voice-orb").setAttribute("aria-label", ["thinking", "speaking"].includes(s.phase) ? "Interrupt" : "Conversation status");
    byId("voice-no-voice").hidden = Boolean(chosenVoice());
    renderCaption();
  }

  /* Pipkin's reply as a caption: the part already spoken is bright, the rest is dim. */
  function renderCaption() {
    const s = session;
    if (!s) return;
    const box = byId("voice-pipkin");
    box.hidden = !s.pipkin;
    const cut = Math.min(s.saidUpTo, s.pipkin.length);
    // Show only the last ~240 characters so the stage stays calm on long replies.
    const from = Math.max(0, cut - 160);
    byId("voice-said").textContent = (from > 0 ? "… " : "") + s.pipkin.slice(from, cut);
    byId("voice-upcoming").textContent = s.pipkin.slice(cut, cut + 240);
  }

  function renderTimer() {
    const s = session;
    if (!s) return;
    const seconds = Math.floor((performance.now() - s.startedAt) / 1000);
    byId("voice-timer").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  /* ---------- The orb: a soft blob that breathes, follows your voice, and pulses with Pipkin's words ---------- */
  let frame = 0;
  let timerInterval = 0;
  function startVisuals() {
    stopVisuals();
    timerInterval = setInterval(renderTimer, 500);
    renderTimer();
    const canvas = byId("voice-canvas");
    const ctx = canvas.getContext("2d");
    const style = getComputedStyle(document.documentElement);
    const accent = style.getPropertyValue("--accent").trim() || "#128a5f";
    const accent2 = style.getPropertyValue("--accent-2").trim() || "#3cc9bd";
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let level = 0;
    const draw = (now) => {
      frame = requestAnimationFrame(draw);
      const s = session;
      if (!s) return;
      const t = reduced ? 0 : now / 1000;
      const phase = s.muted ? "muted" : s.phase;
      let target = 0.05;
      if (phase === "listening" || phase === "hearing") target = Math.min(1, Math.sqrt(s.micLevel) * 3.2);
      else if (phase === "speaking") target = 0.25 + s.energy * 0.55;
      else if (phase === "thinking" || phase === "transcribing") target = 0.18 + 0.12 * Math.sin(t * 3);
      s.energy *= 0.9;
      level += (target - level) * 0.18;
      const w = canvas.width, h = canvas.height, cx = w / 2, cy = h / 2;
      ctx.clearRect(0, 0, w, h);
      const base = w * (phase === "muted" ? 0.25 : 0.28) * (1 + level * 0.22);
      // Outer glow
      const glow = ctx.createRadialGradient(cx, cy, base * 0.6, cx, cy, base * 1.75);
      glow.addColorStop(0, hexA(accent, 0.30 + level * 0.25));
      glow.addColorStop(1, hexA(accent, 0));
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, w, h);
      // Wobbling blob
      ctx.beginPath();
      const points = 96;
      for (let i = 0; i <= points; i++) {
        const a = (i / points) * Math.PI * 2;
        const wobble = Math.sin(a * 3 + t * 1.6) * 0.035 + Math.sin(a * 5 - t * 2.3) * 0.025 * (0.4 + level)
          + Math.sin(a * 2 + t * 0.9) * 0.02;
        const r = base * (1 + wobble * (phase === "muted" ? 0.3 : 1 + level * 1.5));
        const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      const fill = ctx.createLinearGradient(cx - base, cy - base, cx + base, cy + base);
      fill.addColorStop(0, phase === "muted" ? "#8a8f88" : accent2);
      fill.addColorStop(1, phase === "muted" ? "#5d625b" : accent);
      ctx.fillStyle = fill;
      ctx.fill();
      // Moving highlight, like light on a glass bead
      const hx = cx + Math.cos(t * 0.7) * base * 0.35, hy = cy - base * 0.35 + Math.sin(t * 0.9) * base * 0.12;
      const shine = ctx.createRadialGradient(hx, hy, 0, hx, hy, base * 0.9);
      shine.addColorStop(0, "rgba(255,255,255,0.55)");
      shine.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = shine;
      ctx.fill();
      // Thinking: a thin orbiting arc
      if (phase === "thinking" || phase === "transcribing") {
        ctx.beginPath();
        ctx.arc(cx, cy, base * 1.22, t * 2.4, t * 2.4 + Math.PI * 0.6);
        ctx.strokeStyle = hexA(accent2, 0.85);
        ctx.lineWidth = w * 0.012;
        ctx.lineCap = "round";
        ctx.stroke();
      }
    };
    frame = requestAnimationFrame(draw);
  }

  function stopVisuals() {
    cancelAnimationFrame(frame);
    clearInterval(timerInterval);
    frame = timerInterval = 0;
  }

  function hexA(color, alpha) {
    const m = /^#?([0-9a-f]{6})$/i.exec(color.trim());
    if (!m) return `rgba(18,138,95,${alpha})`;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${alpha.toFixed(3)})`;
  }

  function renderSettings() {
    const box = byId("voice-conversation");
    if (!box || !deps) return;
    const current = settings();
    box.checked = current.enabled;
    byId("voice-chat").hidden = !current.enabled;
    const row = byId("voice-speaker-row");
    row.hidden = !current.enabled;
    const select = byId("voice-speaker");
    const voices = localVoices();
    const chosen = chosenVoice();
    const sorted = [...voices].sort((a, b) => quality(b) - quality(a) || a.name.localeCompare(b.name)).filter((v) => quality(v) >= 0);
    select.replaceChildren(...sorted.map((voice) => el("option", { value: voice.voiceURI, text: voiceLabel(voice) })));
    if (chosen) select.value = chosen.voiceURI;
    select.disabled = !voices.length;
    const natural = voices.some((v) => quality(v) >= 2);
    byId("voice-speaker-note").textContent = !voices.length ? "No on-device voices found. Pipkin will show replies as text."
      : natural ? "Only voices that run on this computer are offered. High-quality voices are listed first."
      : deps.isMac ? "For a far more natural voice, download a Premium voice (for example Ava or Zoe): System Settings › Accessibility › Spoken Content › System voice › Manage Voices. Then reopen this page."
      : "For a more natural voice, add a natural voice in Windows Settings › Time & language › Speech. Then reopen this page.";
    byId("voice-rate").value = String(current.rate);
    byId("voice-volume").value = String(Math.round(current.volume * 100));
  }

  function init(options) {
    deps = options;
    byId("voice-chat").addEventListener("click", () => (session ? close() : void open()));
    byId("voice-end").addEventListener("click", close);
    byId("voice-mute").addEventListener("click", toggleMute);
    byId("voice-speaker-toggle").addEventListener("click", () => {
      const on = !settings().speaker;
      deps.saveSettings({ voiceSpeaker: on });
      if (!on && session) {
        speechSynthesis.cancel();
        session.queue = 0;
        session.saidUpTo = session.pipkin.length;
        maybeListen(session.gen);
      }
      render();
    });
    byId("voice-speed").addEventListener("change", (event) => deps.saveSettings({ voiceRate: Number(event.target.value) }));
    byId("voice-settings").addEventListener("click", () => deps.openSettings("tab-voice"));
    byId("voice-volume").addEventListener("input", (event) => deps.saveSettings({ voiceVolume: Number(event.target.value) / 100 }));
    byId("voice-orb").addEventListener("click", interrupt);
    byId("voice-conversation").addEventListener("change", (event) => {
      deps.saveSettings({ voiceConversation: event.target.checked });
      if (!event.target.checked) close();
      renderSettings();
    });
    byId("voice-speaker").addEventListener("change", (event) => deps.saveSettings({ voiceName: event.target.value }));
    byId("voice-rate").addEventListener("change", (event) => {
      deps.saveSettings({ voiceRate: Number(event.target.value) });
      byId("voice-speed").value = event.target.value;
    });
    byId("voice-preview").addEventListener("click", () => {
      const voice = chosenVoice();
      if (!voice) return;
      speechSynthesis.cancel();
      const sample = new SpeechSynthesisUtterance("Hi, I'm Pipkin. This is how I'll sound.");
      sample.voice = voice;
      sample.rate = settings().rate;
      sample.volume = settings().volume;
      speechSynthesis.speak(sample);
    });
    document.addEventListener("keydown", (event) => {
      if (!session || document.querySelector("dialog[open]")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (["thinking", "speaking", "transcribing"].includes(session.phase)) interrupt();
        else close();
      } else if (event.key === " " && !event.target.closest("input, textarea, select, button")) {
        event.preventDefault();
        interrupt();
      }
    }, true);
    window.addEventListener("pagehide", close);
    if ("speechSynthesis" in window) speechSynthesis.addEventListener("voiceschanged", renderSettings);
    renderSettings();
  }

  window.PipkinVoice = {
    init,
    renderSettings: () => deps && renderSettings(),
    toggle: () => deps && settings().enabled && (session ? close() : void open()),
    get active() { return Boolean(session); },
    speakable,
  };
})();
