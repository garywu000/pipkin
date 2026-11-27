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
  let opening = false;
  let previewing = false;
  let openGeneration = 0;
  let natural = null;  // status of the natural (Kokoro) voice pack
  let naturalPoll = 0;
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
    const wanted = settings().voice.startsWith("kokoro:") ? "" : settings().voice;
    const lang = (navigator.language || "en").slice(0, 2);
    const ranked = voices.filter((v) => v.lang.startsWith(lang)).sort((a, b) => quality(b) - quality(a) || (b.default - a.default));
    return voices.find((v) => v.voiceURI === wanted) || ranked[0] || voices[0] || null;
  }

  function voiceLabel(voice) {
    const q = quality(voice);
    return `${voice.name}${q >= 2 && !/premium|enhanced|natural/i.test(voice.name) ? " (high quality)" : ""} · ${voice.lang}`;
  }

  const NON_LATIN_SCRIPT = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Script=Cyrillic}\p{Script=Arabic}\p{Script=Hebrew}\p{Script=Devanagari}\p{Script=Thai}\p{Script=Greek}]/u;
  const ABBREVIATIONS = /\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|No|Fig|Jan|Feb|Mar|Apr|Aug|Sept|Sep|Oct|Nov|Dec|[A-Za-z]|e\.g|i\.e|a\.m|p\.m|U\.S|U\.K)\.$/i;

  /* Turn Markdown into words worth saying. Code, tables, LaTeX and links stay on screen. */
  function speakable(markdown) {
    return String(markdown || "")
      .replace(/```[\s\S]*?(```|$)/g, " (the code is on screen) ")
      .replace(/<think>[\s\S]*?(<\/think>|$)/g, " ")
      .replace(/\$\$[\s\S]*?\$\$/g, " (the formula is on screen) ")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/https?:\/\/\S+/g, " a link ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^\s*[-*_]{3,}\s*$/gm, " ")
      .replace(/^\s*\|.*\|\s*$/gm, " ")
      .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
      .replace(/[*_~#]+/g, "")
      .replace(/\p{Extended_Pictographic}/gu, " ")
      .replace(/\s+/g, " ");
  }

  /* ---------- Natural voice (Kokoro, on this device) ---------- */
  async function naturalPost(path, body = {}, signal) {
    const response = await fetch("/api/voice/natural/" + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "The natural voice isn't available right now.");
    }
    return response;
  }

  async function refreshNatural() {
    try {
      natural = await (await naturalPost("status")).json();
    } catch {
      natural = null;
    }
    if (deps) renderSettings();
    clearTimeout(naturalPoll);
    if (natural?.download?.state === "downloading") naturalPoll = setTimeout(refreshNatural, 800);
    return natural;
  }

  /* The natural voice to use, or null for the system voice. Natural is the default once it's installed. */
  function naturalChoice() {
    if (!natural?.engine || !natural.installed) return null;
    const wanted = settings().voice;
    if (wanted.startsWith("kokoro:")) {
      const id = wanted.slice(7);
      return natural.voices.some((v) => v.id === id) ? id : natural.default;
    }
    return wanted ? null : natural.default;
  }

  async function naturalAudio(text, voice, context, signal, rate = settings().rate) {
    const response = await naturalPost("speak", { text, voice, speed: rate }, signal);
    const bytes = await response.arrayBuffer();
    if (signal?.aborted || context.state === "closed") throw new DOMException("Aborted", "AbortError");
    return context.decodeAudioData(bytes);
  }

  /* ---------- Session ---------- */
  async function open() {
    if (session || opening) return;
    if (previewing) { deps.announce("Wait for the voice preview to finish.", true); return; }
    if (window.PipkinDictation.active) {
      deps.announce("Finish or cancel dictation before starting a conversation.", true);
      return;
    }
    const busyMsg = deps.busyReason?.() || (deps.isBusy() ? "Finish the current reply before starting a conversation." : null);
    if (busyMsg) {
      deps.announce(busyMsg, true);
      return;
    }
    opening = true;
    const attempt = ++openGeneration;
    try {
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
      if (attempt !== openGeneration) return;
      if (!window.PipkinDictation.ready()) {
        deps.announce("Conversation mode uses your dictation model. Download one in Settings › Voice first.", true);
        deps.openSettings("tab-voice");
        return;
      }
      await refreshNatural();
      if (attempt !== openGeneration || !settings().enabled || deps.isBusy() || window.PipkinDictation.active) return;
      const warmVoice = naturalChoice();
      if (warmVoice) void naturalPost("warm", { voice: warmVoice }).catch(() => {});
      session = { gen: 0, phase: "starting", muted: false, you: "", pipkin: "", spoken: 0, queue: 0, replyEnded: false,
        capture: null, controller: null, startedAt: performance.now(), saidUpTo: 0, energy: 0, micLevel: 0,
        lines: [], playing: null, output: null, analyser: null, speech: null };
      deps.composer.dataset.voice = "on";
      byId("voice-overlay").hidden = false;
      byId("voice-speed").value = String(settings().rate);
      startVisuals();
      render();
      const current = session;
      await startCapture(current);
      if (session === current) listen();
    } catch (error) {
      if (attempt === openGeneration) {
        close();
        deps.announce(micProblem(error), true);
      }
    } finally {
      opening = false;
    }
  }

  function close() {
    openGeneration += 1;
    if (!session) return;
    const current = session;
    session = null;
    current.gen += 1;
    current.controller?.abort();
    current.replyController?.abort();
    stopNatural(current);
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    void current.output?.close().catch(() => {});
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

  async function startCapture(current) {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    let context;
    try {
      if (session !== current) return;
      context = new AudioContext();
      if (context.state === "suspended") await context.resume();
      await context.audioWorklet.addModule("/dictation-worklet.js");
      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, "pipkin-capture");
      const mute = context.createGain();
      mute.gain.value = 0;
      source.connect(node).connect(mute).connect(context.destination);
      if (session !== current) return;
      current.capture = { stream, context, node, floor: 0.004, recent: [], utterance: null, loud: 0, quietMs: 0 };
      node.port.onmessage = ({ data }) => { if (session === current && data instanceof Float32Array) onAudio(data); };
      for (const track of stream.getAudioTracks()) track.addEventListener("ended", () => { if (session === current) { close(); deps.announce("The microphone was disconnected.", true); } });
    } finally {
      if (!current.capture) {
        stream.getTracks().forEach((track) => track.stop());
        if (context) void context.close().catch(() => {});
      }
    }
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
    session.resumePending = false;
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
      s.notice = error.message || "Couldn't transcribe that.";
      listen();
      return;
    }
    if (session !== s || gen !== s.gen) return;
    if (text.replace(/[^\p{L}\p{N}]/gu, "").length < 2) {
      listen();
      return;
    }
    s.you = text;
    s.notice = "";
    s.pipkin = "";
    s.spoken = 0;
    s.queuedPrefix = "";
    s.speechRevised = false;
    s.saidUpTo = 0;
    s.replyEnded = false;
    s.phase = "thinking";
    render();
    const observer = {
      delta: (answer) => { if (session === s && gen === s.gen) onReply(answer, false); },
      end: (answer, failed) => {
        if (session !== s || gen !== s.gen || s.replyEnded) return;
        s.replyEnded = true;
        if (answer === null || !String(answer || "").trim()) {
          s.pipkin = s.pipkin || "That reply didn't finish. Try again, or type instead.";
          render();
          maybeListen(gen);
          return;
        }
        onReply(answer, true);
      },
    };
    try {
      s.replyController = new AbortController();
      const pending = Promise.resolve(deps.send(text, observer, s.replyController.signal));
      s.replyPending = pending;
      try { await pending; }
      finally { if (s.replyPending === pending) s.replyPending = null; }
    } catch (error) {
      if (session !== s || gen !== s.gen) return;
      s.notice = error.message;
      s.replyEnded = true;
      render();
      setTimeout(() => { if (session === s && gen === s.gen) listen(); }, 1200);
    }
  }

  /* Keep chunks small enough for the local model and preserve their playback order without splitting abbreviations. */
  function speechChunks(text, finished) {
    const chunks = [];
    let offset = 0;
    const boundaryRe = /[.!?](?=\s|$)|[。！？]/g;
    while (offset < text.length) {
      const rest = text.slice(offset);
      boundaryRe.lastIndex = 0;
      let length = 0;
      let match;
      while ((match = boundaryRe.exec(rest))) {
        const end = match.index + 1;
        if (!finished && end >= rest.length) break;
        const candidate = rest.slice(0, end);
        if (match[0] === "." && ABBREVIATIONS.test(candidate)) continue;
        length = end;
        break;
      }
      if (rest.length > 500 && (!length || length > 500)) {
        const space = rest.lastIndexOf(" ", 500);
        length = space > 100 ? space : 500;
        if (/[\uD800-\uDBFF]/.test(rest[length - 1]) && /[\uDC00-\uDFFF]/.test(rest[length])) length -= 1;
      }
      if (!length && finished) length = rest.length;
      if (!length) break;
      chunks.push(rest.slice(0, length));
      offset += length;
    }
    return chunks;
  }

  function onReply(answer, finished) {
    const s = session;
    const plain = speakable(answer);
    s.pipkin = plain;
    // Never speak a stale suffix when the model revises an earlier part of its streamed reply.
    if (s.queuedPrefix && !plain.startsWith(s.queuedPrefix)) {
      stopNatural(s);
      if ("speechSynthesis" in window) speechSynthesis.cancel();
      s.queue = 0;
      s.speechRevised = true;
      s.notice = "The reply changed while streaming. The final text is in the chat.";
    }
    if (!s.speechRevised) {
      for (const chunk of speechChunks(plain.slice(s.spoken), finished)) {
        const start = s.spoken;
        s.spoken += chunk.length;
        s.queuedPrefix = plain.slice(0, s.spoken);
        say(chunk, s.gen, start);
      }
    } else if (finished) {
      s.saidUpTo = plain.length;
    }
    if (finished) maybeListen(s.gen);
    render();
  }

  function say(text, gen, start) {
    const s = session;
    const { rate, volume, speaker } = settings();
    const voice = chosenVoice();
    // Kokoro's bundled lexicons are English-only; fall back to system voice only for non-Latin scripts.
    const naturalVoice = NON_LATIN_SCRIPT.test(text) ? null : naturalChoice();
    if (speaker && volume && text.trim() && !voice && !naturalVoice) s.notice = "No suitable voice is available. The reply is still in the chat.";
    if (!text.trim() || !speaker || !volume || (!voice && !naturalVoice)) {
      s.saidUpTo = Math.max(s.saidUpTo, start + text.length);
      return;
    }
    s.lines.push({ text, start, end: start + text.length, gen, voice, naturalVoice, rate, volume });
    s.queue += 1;
    // If sentence #1 is already playing, immediately start synthesizing sentence #2 in parallel.
    if (s.playing && s.lines.length === 1) prefetch(s, s.lines[0]);
    void playNext(s);
  }

  function prefetch(s, line) {
    if (!line?.naturalVoice || line.audio) return;
    if (!s.output) {
      s.output = new AudioContext();
      s.analyser = s.output.createAnalyser();
      s.analyser.fftSize = 512;
      s.gain = s.output.createGain();
      s.gain.connect(s.analyser).connect(s.output.destination);
    }
    if (!s.speech || s.speech.signal.aborted) s.speech = new AbortController();
    line.audio = naturalAudio(line.text, line.naturalVoice, s.output, s.speech.signal, line.rate).catch((error) => error);
  }

  async function playNext(s) {
    if (s.playing || session !== s) return;
    const line = s.lines.shift();
    if (!line) return;
    s.playing = line;
    const valid = () => session === s && line.gen === s.gen && s.playing === line;
    const done = () => {
      if (!valid()) return;
      clearTimeout(line.watchdog);
      clearInterval(line.timer);
      s.playing = null;
      s.saidUpTo = Math.max(s.saidUpTo, line.end);
      s.queue = Math.max(0, s.queue - 1);
      render();
      maybeListen(line.gen);
      void playNext(s);
    };
    try {
      prefetch(s, line);
      const buffer = line.audio ? await line.audio : null;
      if (!valid()) return;
      s.phase = "speaking";
      render();
      if (buffer instanceof AudioBuffer) {
        if (s.output.state === "suspended") await s.output.resume();
        if (!valid()) return;
        s.gain.gain.value = line.volume;
        const source = s.output.createBufferSource();
        source.buffer = buffer;
        source.connect(s.gain);
        source.onended = done;
        line.source = source;
        const began = s.output.currentTime;
        line.timer = setInterval(() => {
          if (!valid()) return;
          const progress = Math.min(1, (s.output.currentTime - began) / buffer.duration);
          s.saidUpTo = Math.max(s.saidUpTo, Math.round(line.start + progress * (line.end - line.start)));
          renderCaption();
        }, 120);
        source.start();
      } else if (line.voice && buffer?.name !== "AbortError") {
        if (line.naturalVoice) s.notice = "Natural voice unavailable for this sentence. Using the system voice.";
        const utterance = new SpeechSynthesisUtterance(line.text);
        utterance.voice = line.voice;
        utterance.lang = line.voice.lang;
        utterance.rate = Math.min(1.6, Math.max(0.7, line.rate));
        utterance.volume = line.volume;
        utterance.onboundary = (event) => {
          if (!valid()) return;
          s.saidUpTo = Math.max(s.saidUpTo, line.start + event.charIndex + (event.charLength || 1));
          s.energy = 1;
          renderCaption();
        };
        utterance.onend = done;
        utterance.onerror = () => { if (valid()) s.notice = "Voice playback failed. The reply is still in the chat."; done(); };
        line.watchdog = setTimeout(() => { if (valid()) { speechSynthesis.cancel(); done(); } }, Math.max(15000, line.text.length * 180));
        speechSynthesis.speak(utterance);
      } else {
        s.notice = "Voice unavailable for this sentence. The reply is still in the chat.";
        done();
        return;
      }
      // At most one sentence is synthesized ahead of playback.
      prefetch(s, s.lines[0]);
    } catch (error) {
      if (valid()) s.notice = "Voice playback failed. The reply is still in the chat.";
      done();
    }
  }

  function stopNatural(s) {
    s.speech?.abort();
    s.speech = null;
    const playing = s.playing;
    s.playing = null;
    s.lines = [];
    if (playing) {
      clearTimeout(playing.watchdog);
      clearInterval(playing.timer);
      try { playing.source?.stop(); } catch { /* not started */ }
    }
  }

  function maybeListen(gen) {
    const s = session;
    if (!s || gen !== s.gen || !s.replyEnded || s.queue > 0 || s.resumePending) return;
    s.resumePending = true;
    setTimeout(() => { if (session === s && gen === s.gen && s.queue === 0) listen(); }, 350);
  }

  function interrupt() {
    const s = session;
    if (!s || !["thinking", "speaking", "transcribing"].includes(s.phase)) return;
    s.gen += 1;
    s.controller?.abort();
    s.replyController?.abort();
    stopNatural(s);
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    s.queue = 0;
    s.replyEnded = true;
    const pending = s.replyPending;
    if (pending) {
      const gen = s.gen;
      s.phase = "thinking";
      s.notice = "Stopping the previous reply…";
      render();
      const resume = () => {
        if (session !== s || gen !== s.gen) return;
        s.notice = "";
        listen();
      };
      void pending.then(resume, resume);
    } else {
      listen();
    }
  }

  function toggleMute() {
    if (!session) return;
    session.muted = !session.muted;
    if (session.muted && session.capture) {
      session.capture.utterance = null;
      session.capture.recent = [];
      session.capture.loud = 0;
      if (session.phase === "hearing") session.phase = "listening";
    }
    render();
  }

  function sendNow() {
    const s = session;
    if (!s || s.phase !== "hearing" || !s.capture?.utterance) return;
    const utterance = s.capture.utterance;
    s.capture.utterance = null;
    s.capture.loud = 0;
    void heard(utterance);
  }

  function orbAction() {
    const s = session;
    if (!s) return;
    if (s.muted) { toggleMute(); return; }
    if (s.phase === "hearing") { sendNow(); return; }
    if (["thinking", "speaking", "transcribing"].includes(s.phase)) interrupt();
  }

  function activeVoiceLabel() {
    const nat = naturalChoice();
    if (nat) {
      const info = natural?.voices?.find((v) => v.id === nat);
      return info ? `${info.label} · Natural` : "Natural voice";
    }
    const sys = chosenVoice();
    return sys ? sys.name : "";
  }

  /* ---------- UI ---------- */
  const PHASES = {
    starting: "Starting the microphone…", listening: "Listening. Ask anything.", hearing: "Listening…",
    transcribing: "Got it…", thinking: "Thinking…", speaking: "Pipkin is speaking",
  };

  function render() {
    const s = session;
    if (!s) return;
    const overlay = byId("voice-overlay");
    overlay.dataset.phase = s.muted ? "muted" : s.phase;
    const listening = !s.muted && ["listening", "hearing"].includes(s.phase);
    for (const track of s.capture?.stream.getAudioTracks() || []) track.enabled = listening;
    byId("voice-notice").textContent = s.notice || "";
    byId("voice-notice").hidden = !s.notice;
    const phaseText = s.muted ? "Microphone paused" : PHASES[s.phase] || "";
    byId("voice-phase").textContent = phaseText;
    byId("voice-phase").hidden = !phaseText;
    const actionBtn = byId("voice-action");
    if (actionBtn) {
      const actionLabel = s.muted ? "Resume mic"
        : s.phase === "hearing" ? "Send now"
        : ["thinking", "speaking", "transcribing"].includes(s.phase) ? "Interrupt" : "";
      actionBtn.textContent = actionLabel;
      actionBtn.hidden = !actionLabel;
    }
    const voicePill = byId("voice-active-pill");
    if (voicePill) {
      const label = activeVoiceLabel();
      voicePill.textContent = label;
      voicePill.hidden = !label;
    }
    byId("voice-you").textContent = s.you;
    byId("voice-you").hidden = !s.you;
    const mute = byId("voice-mute");
    mute.setAttribute("aria-pressed", String(s.muted));
    mute.title = s.muted ? "Resume listening" : "Pause the microphone";
    mute.setAttribute("aria-label", mute.title);
    const speakerOn = settings().speaker;
    const speakerButton = byId("voice-speaker-toggle");
    speakerButton.setAttribute("aria-pressed", String(speakerOn));
    speakerButton.title = speakerOn ? "Turn Pipkin's voice off (captions only)" : "Turn Pipkin's voice on";
    speakerButton.setAttribute("aria-label", speakerButton.title);
    byId("voice-orb").setAttribute("aria-label", s.muted ? "Resume microphone" : s.phase === "hearing" ? "Send now"
      : ["thinking", "speaking", "transcribing"].includes(s.phase) ? "Interrupt" : "Conversation status");
    byId("voice-no-voice").hidden = Boolean(chosenVoice() || naturalChoice());
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
    const analyserData = new Float32Array(512);
    let level = 0;
    const draw = (now) => {
      frame = requestAnimationFrame(draw);
      const s = session;
      if (!s || document.hidden) return;
      const t = reduced ? 0 : now / 1000;
      const phase = s.muted ? "muted" : s.phase;
      let target = 0.05;
      if (phase === "listening") target = Math.min(0.45, Math.sqrt(s.micLevel) * 2.4);
      else if (phase === "hearing") target = Math.min(1, 0.22 + Math.sqrt(s.micLevel) * 3.6);
      else if (phase === "speaking") target = 0.28 + s.energy * 0.62;
      else if (phase === "thinking" || phase === "transcribing") target = 0.18 + 0.12 * Math.sin(t * 3);
      if (s.playing?.source && s.analyser) {
        s.analyser.getFloatTimeDomainData(analyserData);
        let sum = 0;
        for (let i = 0; i < analyserData.length; i += 4) sum += analyserData[i] * analyserData[i];
        s.energy = Math.max(s.energy, Math.min(1, Math.sqrt(sum / (analyserData.length / 4)) * 5));
      }
      s.energy *= 0.88;
      level += (target - level) * 0.2;
      const w = canvas.width, h = canvas.height, cx = w / 2, cy = h / 2;
      ctx.clearRect(0, 0, w, h);
      const base = w * (phase === "muted" ? 0.24 : 0.275) * (1 + level * 0.22);
      // Outer ambient glow
      const glow = ctx.createRadialGradient(cx, cy, base * 0.55, cx, cy, base * 1.82);
      glow.addColorStop(0, hexA(phase === "muted" ? "#7e857d" : accent, phase === "muted" ? 0.14 : 0.30 + level * 0.28));
      glow.addColorStop(1, hexA(accent, 0));
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, w, h);
      // Hearing & Speaking harmonic wave rings
      if (!reduced && (phase === "hearing" || phase === "speaking")) {
        for (let ring = 0; ring < 2; ring += 1) {
          const pulse = ((t * (phase === "speaking" ? 1.1 : 0.85) + ring * 0.5) % 1);
          const radius = base * (1.06 + pulse * (0.42 + level * 0.32));
          ctx.beginPath();
          ctx.arc(cx, cy, radius, 0, Math.PI * 2);
          ctx.strokeStyle = hexA(phase === "speaking" ? accent2 : accent, (1 - pulse) * (0.38 + level * 0.35));
          ctx.lineWidth = w * 0.009;
          ctx.stroke();
        }
      }
      // Wobbling glass-bead orb
      ctx.beginPath();
      const points = 96;
      for (let i = 0; i <= points; i++) {
        const a = (i / points) * Math.PI * 2;
        const wobble = Math.sin(a * 3 + t * 1.6) * 0.035 + Math.sin(a * 5 - t * 2.3) * 0.025 * (0.4 + level)
          + Math.sin(a * 2 + t * 0.9) * 0.02;
        const r = base * (1 + wobble * (phase === "muted" ? 0.25 : 1 + level * 1.55));
        const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      const fill = ctx.createLinearGradient(cx - base, cy - base, cx + base, cy + base);
      fill.addColorStop(0, phase === "muted" ? "#8a8f88" : accent2);
      fill.addColorStop(1, phase === "muted" ? "#5d625b" : accent);
      ctx.fillStyle = fill;
      ctx.fill();
      // Moving specular highlight
      const hx = cx + Math.cos(t * 0.7) * base * 0.34, hy = cy - base * 0.34 + Math.sin(t * 0.9) * base * 0.12;
      const shine = ctx.createRadialGradient(hx, hy, 0, hx, hy, base * 0.9);
      shine.addColorStop(0, "rgba(255,255,255,0.58)");
      shine.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = shine;
      ctx.fill();
      // Thinking / Transcribing: dual opposing orbital arcs
      if (phase === "thinking" || phase === "transcribing") {
        ctx.lineCap = "round";
        ctx.lineWidth = w * 0.012;
        ctx.beginPath();
        ctx.arc(cx, cy, base * 1.22, t * 2.5, t * 2.5 + Math.PI * 0.62);
        ctx.strokeStyle = hexA(accent2, 0.88);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx, cy, base * 1.32, -t * 1.7, -t * 1.7 + Math.PI * 0.45);
        ctx.strokeStyle = hexA(accent, 0.52);
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
    const convBody = byId("voice-conversation-body");
    if (convBody) convBody.hidden = !current.enabled;
    const row = byId("voice-speaker-row");
    row.hidden = !current.enabled;
    byId("voice-natural-row").hidden = !current.enabled;
    renderNaturalCard();
    const select = byId("voice-speaker");
    const voices = localVoices();
    const chosen = chosenVoice();
    const sorted = [...voices].sort((a, b) => quality(b) - quality(a) || a.name.localeCompare(b.name)).filter((v) => quality(v) >= 0);
    const groups = [];
    if (natural?.engine && natural.installed) {
      groups.push(el("optgroup", { label: "Natural voices" },
        ...natural.voices.map((v) => el("option", { value: "kokoro:" + v.id, text: `${v.label} · ${v.detail}` }))));
    }
    groups.push(el("optgroup", { label: "System voices" },
      ...sorted.map((voice) => el("option", { value: voice.voiceURI, text: voiceLabel(voice) }))));
    select.replaceChildren(...groups);
    const naturalPick = naturalChoice();
    select.value = naturalPick ? "kokoro:" + naturalPick : chosen ? chosen.voiceURI : "";
    select.disabled = !voices.length && !naturalPick;
    const highQuality = Boolean(naturalPick) || voices.some((v) => quality(v) >= 2);
    byId("voice-speaker-note").textContent = naturalPick ? "Natural voices run on this computer with Kokoro. Pick a system voice to use that instead."
      : !voices.length ? "No on-device voices found. Pipkin will show replies as text."
      : highQuality ? "Only voices that run on this computer are offered. High-quality voices are listed first."
      : deps.isMac ? "For a far more natural voice, download a Premium voice (for example Ava or Zoe): System Settings › Accessibility › Spoken Content › System voice › Manage Voices. Then reopen this page."
      : "For a more natural voice, add a natural voice in Windows Settings › Time & language › Speech. Then reopen this page.";
    byId("voice-rate").value = String(current.rate);
    byId("voice-volume").value = String(Math.round(current.volume * 100));
  }

  function renderNaturalCard() {
    const actions = byId("voice-natural-actions");
    const note = byId("voice-natural-note");
    if (!actions) return;
    const mb = natural ? Math.round(natural.size / 1e6) : 132;
    actions.replaceChildren();
    if (!natural) {
      note.textContent = "Checking the natural voice…";
      return;
    }
    if (!natural.engine) {
      note.textContent = "This build of Pipkin doesn't include the natural voice engine.";
      return;
    }
    const job = natural.download;
    if (job?.state === "downloading") {
      const percent = Math.floor((job.received / job.size) * 100);
      note.textContent = "Downloading the natural voice… You can keep using Pipkin.";
      actions.append(el("span", { className: "voice-progress", text: `${percent}%` },
        el("span", { className: "voice-progress-bar", style: `--progress:${percent}%` })),
      el("button", { type: "button", className: "button small", text: "Cancel",
        onclick: async () => {
          try { natural = await (await naturalPost("cancel")).json(); renderSettings(); }
          catch (error) { deps.announce(error.message, true); }
        } }));
    } else if (natural.installed) {
      const diskNote = natural.diskBytes ? ` (${Math.round(natural.diskBytes / 1e6)} MB on disk)` : "";
      note.classList.remove("voice-error");
      note.textContent = `Installed${diskNote}. Eight natural English voices that run on this computer. Pick one below.`;
      actions.append(el("span", { className: "task-status-pill", dataset: { status: "active" }, text: "Installed" }),
        el("button", { type: "button", className: "button small", text: "Remove", onclick: async () => {
          if (session || opening || previewing) { deps.announce("End the conversation or preview before removing a voice.", true); return; }
          try { natural = await (await naturalPost("remove")).json(); renderSettings(); }
          catch (error) { deps.announce(error.message, true); }
        } }));
    } else {
      const failed = job?.state === "failed";
      note.classList.toggle("voice-error", Boolean(failed));
      note.textContent = failed ? job.error
        : `Human-sounding voices made on this computer (Kokoro). A one-time ${mb} MB download; speech synthesis runs locally.`;
      actions.append(el("button", { type: "button", className: "button small accent", text: `${failed ? "Retry" : "Download"} · ${mb} MB`, onclick: async () => {
        try {
          natural = await (await naturalPost("download")).json();
        } catch (error) {
          deps.announce(error.message, true);
        }
        void refreshNatural();
      } }));
    }
  }

  function init(options) {
    deps = options;
    byId("voice-chat").addEventListener("click", () => ((session || opening) ? close() : void open()));
    byId("voice-end").addEventListener("click", close);
    byId("voice-mute").addEventListener("click", toggleMute);
    byId("voice-speaker-toggle").addEventListener("click", () => {
      const on = !settings().speaker;
      deps.saveSettings({ voiceSpeaker: on });
      if (!on && session) {
        stopNatural(session);
        if ("speechSynthesis" in window) speechSynthesis.cancel();
        session.queue = 0;
        session.saidUpTo = session.pipkin.length;
        maybeListen(session.gen);
      }
      render();
    });
    byId("voice-speed").addEventListener("change", (event) => {
      deps.saveSettings({ voiceRate: Number(event.target.value) });
      byId("voice-rate").value = event.target.value;
    });
    byId("voice-settings").addEventListener("click", () => deps.openSettings("tab-voice"));
    byId("voice-active-pill")?.addEventListener("click", () => deps.openSettings("tab-voice"));
    byId("voice-volume").addEventListener("input", (event) => {
      const vol = Number(event.target.value) / 100;
      deps.saveSettings({ voiceVolume: vol });
      if (session?.gain) session.gain.gain.value = vol;
    });
    byId("voice-orb").addEventListener("click", orbAction);
    byId("voice-action")?.addEventListener("click", orbAction);
    byId("voice-conversation").addEventListener("change", (event) => {
      deps.saveSettings({ voiceConversation: event.target.checked });
      if (!event.target.checked) close();
      renderSettings();
    });
    byId("voice-speaker").addEventListener("change", (event) => {
      deps.saveSettings({ voiceName: event.target.value });
      if (event.target.value.startsWith("kokoro:")) {
        void naturalPost("warm", { voice: event.target.value.slice(7) }).catch(() => {});
      }
      renderSettings();
      if (session) render();
    });
    byId("voice-rate").addEventListener("change", (event) => {
      deps.saveSettings({ voiceRate: Number(event.target.value) });
      byId("voice-speed").value = event.target.value;
    });
    let previewCancel = null;
    byId("voice-preview").addEventListener("click", async () => {
      if (previewCancel) { previewCancel(); return; }
      if (session || opening) { deps.announce("End the conversation before previewing a voice.", true); return; }
      if (previewing) return;
      previewing = true;
      const button = byId("voice-preview");
      button.textContent = "Loading…";
      let context;
      let watchdog;
      let source;
      const abort = new AbortController();
      previewCancel = () => {
        abort.abort();
        try { source?.stop(); } catch { /* already stopped */ }
        if ("speechSynthesis" in window) speechSynthesis.cancel();
      };
      try {
        const naturalPick = naturalChoice();
        if (naturalPick) {
          context = new AudioContext();
          if (context.state === "suspended") await context.resume();
          const buffer = await naturalAudio("Hi, I'm Pipkin. This is how I'll sound when we talk.", naturalPick, context, abort.signal);
          if (abort.signal.aborted) return;
          button.textContent = "Stop";
          const gain = context.createGain();
          gain.gain.value = settings().volume;
          source = context.createBufferSource();
          source.buffer = buffer;
          source.connect(gain).connect(context.destination);
          await new Promise((resolve, reject) => {
            source.onended = resolve;
            watchdog = setTimeout(() => reject(new Error("Voice preview timed out.")), Math.max(10000, (buffer.duration + 5) * 1000));
            source.start();
          });
        } else {
          const voice = chosenVoice();
          if (!voice) throw new Error("No on-device voice is available.");
          button.textContent = "Stop";
          const sample = new SpeechSynthesisUtterance("Hi, I'm Pipkin. This is how I'll sound.");
          sample.voice = voice;
          sample.lang = voice.lang;
          sample.rate = settings().rate;
          sample.volume = settings().volume;
          await new Promise((resolve, reject) => {
            sample.onend = resolve;
            sample.onerror = () => (abort.signal.aborted ? resolve() : reject(new Error("Voice preview failed.")));
            watchdog = setTimeout(() => { speechSynthesis.cancel(); reject(new Error("Voice preview timed out.")); }, 15000);
            speechSynthesis.speak(sample);
          });
        }
      } catch (error) {
        if (error?.name !== "AbortError") deps.announce(error.message, true);
      } finally {
        clearTimeout(watchdog);
        previewCancel = null;
        if (context) await context.close().catch(() => {});
        previewing = false;
        button.textContent = "Preview";
      }
    });
    document.addEventListener("keydown", (event) => {
      if (!session || document.querySelector("dialog[open]")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        close();
      } else if (event.key === " " && !event.target.closest("input, textarea, select, button")) {
        event.preventDefault();
        orbAction();
      }
    }, true);
    window.addEventListener("pagehide", close);
    if ("speechSynthesis" in window) speechSynthesis.addEventListener("voiceschanged", renderSettings);
    renderSettings();
    void refreshNatural();
  }

  window.PipkinVoice = {
    init,
    renderSettings: () => deps && (renderSettings(), void refreshNatural()),
    toggle: () => deps && settings().enabled && ((session || opening) ? close() : void open()),
    get active() { return Boolean(session || opening); },
    close,
    speakable, speechChunks,
  };
})();
