"use strict";

/* First-launch Antigravity-inspired welcome animation and optional one-step-at-a-time setup. */
(() => {
  const { el, icon, copyText, formatSize } = window.PipkinUI;
  const host = window.PipkinOnboardingHost;
  if (!host) return;

  const DONE_KEY = "pipkin-onboarded";
  const TOTAL_STEPS = 5;
  const CODE_SHARDS = [
    "agent.run()", "ollama://local", "plan()", "diff +24 -6", "sha256()", "workspace.read()",
    "approve()", "num_ctx: 16K", "127.0.0.1", "stream()", "undo_run()", "tool_call",
    "check_page()", "0x3ED598", "verify()", "local_llm", "write_page()", "checkpoint",
  ];
  const INSTRUCTION_PRESETS = [
    ["Concise & direct", "Be concise and direct. Skip filler and get straight to the answer."],
    ["Software engineer", "I'm a software engineer. Prefer clean, idiomatic code examples and brief explanations."],
    ["Student & writer", "Explain concepts clearly with examples, and keep my own voice when helping with writing."],
  ];
  const MODE_LABELS = { chat: "Chat", work: "Agent" };
  const THEME_LABELS = { system: "System", light: "Light", dark: "Dark" };
  const WEB_LABELS = { auto: "Web: Auto", on: "Web: On", off: "Web: Off" };
  const PERMISSION_LABELS = { read: "Read only", ask: "Ask first", auto: "Auto-edit" };

  let overlay = null;
  let step = 0;
  let direction = "next";
  let draft = null;
  let previewMode = false;
  let modelPoll = 0;

  function markDone() {
    try { localStorage.setItem(DONE_KEY, "1"); } catch { /* ignore */ }
  }

  function themePreviewCard(value, title, subtitle) {
    const selected = draft.theme === value;
    const mini = el("div", { className: "ob-mini ob-mini-" + value, "aria-hidden": "true" },
      el("div", { className: "ob-mini-side" }, el("i"), el("i"), el("i")),
      el("div", { className: "ob-mini-main" },
        el("div", { className: "ob-mini-pill" }),
        el("div", { className: "ob-mini-dot" }),
        el("div", { className: "ob-mini-bar" })));
    const card = el("button", {
      type: "button", className: "ob-theme-card",
      "aria-pressed": String(selected), dataset: { selected: String(selected) },
    }, mini, el("strong", { text: title }), el("small", { text: subtitle }));
    card.addEventListener("click", () => {
      draft.theme = value;
      host.previewTheme(value);
      for (const peer of card.parentElement.children) {
        const active = peer === card;
        peer.dataset.selected = String(active);
        peer.setAttribute("aria-pressed", String(active));
      }
    });
    return card;
  }

  function choiceCard(active, iconName, title, subtitle, onSelect) {
    const card = el("button", {
      type: "button", className: "ob-choice-card",
      "aria-pressed": String(active), dataset: { selected: String(active) },
    },
    iconName ? el("span", { className: "ob-choice-icon" }, icon(iconName)) : null,
    el("span", { className: "ob-choice-copy" }, el("strong", { text: title }), el("small", { text: subtitle })),
    el("span", { className: "ob-choice-check", "aria-hidden": "true" }, icon("check")));
    card.addEventListener("click", () => {
      onSelect();
      for (const peer of card.parentElement.children) {
        const isCurrent = peer === card;
        peer.dataset.selected = String(isCurrent);
        peer.setAttribute("aria-pressed", String(isCurrent));
      }
    });
    return card;
  }

  function plat() {
    return window.PipkinPlatform || {
      isMac: true, osName: "macOS", osBadge: "MACOS", yourDevice: "your Mac",
      ollamaUrl: "https://ollama.com/download/mac", ollamaLabel: "Ollama for macOS",
    };
  }

  function buildWelcomeStep() {
    const P = plat();
    const emblem = el("div", { className: "ob-emblem", "aria-hidden": "true" },
      el("div", { className: "ob-ring ob-ring-1" }),
      el("div", { className: "ob-ring ob-ring-2" }),
      el("div", { className: "ob-emblem-core" },
        el("img", { src: "/favicon.png", alt: "", width: 62, height: 62 })));
    const features = el("div", { className: "ob-pills" },
      el("span", { className: "ob-pill" }, icon("shield"), `Private on ${P.yourDevice}`),
      el("span", { className: "ob-pill" }, icon("bot"), "Chat & Workspace Agent"),
      el("span", { className: "ob-pill" }, icon("check"), "Approve every edit"));
    const start = el("button", { type: "button", className: "button accent ob-cta" },
      el("span", { text: "Get started" }), icon("chevron-right"));
    start.addEventListener("click", () => goTo(1, "next"));
    const skip = el("button", { type: "button", className: "ob-ghost" , text: "Skip setup and jump right in" });
    skip.addEventListener("click", () => finish(false));
    return el("div", { className: "ob-step ob-step-hero" },
      emblem,
      el("span", { className: "ob-eyebrow", text: `LOCAL · PRIVATE · ${P.osBadge}` }),
      el("h1", { className: "ob-title", text: "Welcome to Pipkin" }),
      el("p", { className: "ob-lead", text: "Your private local-model harness: plan, build, verify with full control." }),
      features,
      el("div", { className: "ob-hero-actions" }, start, skip));
  }

  function paletteCard(p) {
    const selected = draft.palette === p.id;
    const swatch = el("span", { className: "ob-swatch", "aria-hidden": "true" });
    swatch.style.setProperty("--sw-a", p.a);
    swatch.style.setProperty("--sw-b", p.b);
    swatch.style.setProperty("--sw-bg", p.bg);
    const card = el("button", {
      type: "button", className: "ob-palette-card",
      "aria-pressed": String(selected), dataset: { selected: String(selected) },
    }, swatch, el("strong", { text: p.name }), el("small", { text: p.note }));
    card.addEventListener("click", () => {
      draft.palette = p.id;
      host.previewPalette?.(p.id);
      for (const peer of card.parentElement.children) {
        const active = peer === card;
        peer.dataset.selected = String(active);
        peer.setAttribute("aria-pressed", String(active));
      }
    });
    return card;
  }

  function buildThemeStep() {
    const P = plat();
    const modeGrid = el("div", { className: "ob-theme-grid" },
      themePreviewCard("system", "System", `Matches ${P.osName}`),
      themePreviewCard("light", "Light", "Luminous porcelain"),
      themePreviewCard("dark", "Dark", "Obsidian glass"));
    const palettes = host.getPalettes?.() || [];
    const paletteGrid = el("div", { className: "ob-palette-grid" }, ...palettes.map(paletteCard));
    const body = el("div", { className: "ob-stack" },
      el("div", { className: "ob-field" }, el("strong", { text: "Appearance" }), modeGrid),
      palettes.length ? el("div", { className: "ob-field" }, el("strong", { text: "Color palette" }), paletteGrid) : null);
    return stepFrame(
      "Choose your look",
      "Try different appearances and natural color palettes live — switch anytime in Settings.",
      body,
      { canSkip: true, onSkip: () => goTo(2, "next"), onNext: () => goTo(2, "next") },
    );
  }

  function buildWorkspaceStep() {
    const info = host.getModels();
    const modelWrap = el("div", { className: "ob-field" },
      el("label", { htmlFor: "ob-model-select" }, el("strong", { text: "Local Ollama model" })));
    const select = el("select", { id: "ob-model-select" });
    const selectBox = el("div", { className: "select-wrap" }, select, icon("chevron"));
    const copyCmd = el("button", { type: "button", className: "button small ob-copy-cmd", "aria-label": "Copy ollama pull command" },
      icon("copy"), el("span", { className: "copy-label", text: "Copy" }));
    copyCmd.addEventListener("click", () => copyText("ollama pull qwen3:8b", copyCmd));
    const guideSummary = el("summary", {},
      icon("bulb"),
      el("span", { text: info.models.length ? "How to install another local model" : "How to install your first local model" }),
      icon("chevron-right", "icon chev-sm"));
    const guide = el("details", { className: "ob-guide", open: !info.models.length },
      guideSummary,
      el("div", { className: "ob-guide-body" },
        el("ol", { className: "ob-guide-steps" },
          el("li", {}, "Install & open ", el("a", { href: plat().ollamaUrl, target: "_blank", rel: "noopener noreferrer", text: plat().ollamaLabel }), "."),
          el("li", {}, plat().isMac ? "Pull a model in Terminal (great starter on 16 GB Macs):" : "Pull a model in PowerShell (great starter on 16 GB PCs):")),
        el("div", { className: "ob-cmd" }, el("code", { text: "ollama pull qwen3:8b" }), copyCmd),
        el("p", { className: "ob-guide-note", text: "Installed models appear in the menu above automatically once Ollama is running." })));
    const populateModels = () => {
      const latest = host.getModels();
      select.replaceChildren();
      if (latest.models.length) {
        for (const item of latest.models) {
          const detail = [item.parameters, item.quantization, formatSize(item.size)].filter(Boolean).join(" · ");
          select.add(new Option(latest.shortModelName(item.name) + (detail ? "  ·  " + detail : ""), item.name));
        }
        select.disabled = false;
        select.value = draft.model || latest.model || latest.models[0].name;
        draft.model = select.value;
        guideSummary.querySelector("span").textContent = "How to install another local model";
      } else {
        select.add(new Option(latest.state === "offline" ? "Ollama not running yet — install or open Ollama" : "No local models found yet", ""));
        select.disabled = true;
        guide.open = true;
        guideSummary.querySelector("span").textContent = "How to install your first local model";
      }
    };
    populateModels();
    select.addEventListener("change", () => { draft.model = select.value; });
    clearInterval(modelPoll);
    if (!info.models.length) {
      modelPoll = setInterval(() => {
        if (!overlay || step !== 2) return clearInterval(modelPoll);
        if (host.getModels().models.length) {
          populateModels();
          clearInterval(modelPoll);
        }
      }, 1500);
    }
    modelWrap.append(selectBox, guide);

    const modeGrid = el("div", { className: "ob-choice-grid two" },
      choiceCard(draft.appMode === "chat", "chat", "Chat", "Private answers, writing & math", () => { draft.appMode = "chat"; }),
      choiceCard(draft.appMode === "work", "bot", "Agent", "Plan, edit files & run checks", () => { draft.appMode = "work"; }));

    const permGrid = el("div", { className: "ob-choice-grid three" },
      choiceCard(draft.permission === "ask", null, "Ask first", "Approve each edit", () => { draft.permission = "ask"; }),
      choiceCard(draft.permission === "read", null, "Read only", "Inspect without edits", () => { draft.permission = "read"; }),
      choiceCard(draft.permission === "auto", null, "Auto-edit", "Edits apply directly", () => { draft.permission = "auto"; }));

    const body = el("div", { className: "ob-stack" },
      modelWrap,
      el("div", { className: "ob-field" }, el("strong", { text: "Default mode" }), modeGrid),
      el("div", { className: "ob-field" }, el("strong", { text: "Agent permissions" }), permGrid));

    return stepFrame(
      "Set up your workspace",
      "Choose your starting model and how Pipkin handles workspace changes.",
      body,
      { canSkip: true, onSkip: () => goTo(3, "next"), onNext: () => goTo(3, "next") },
    );
  }

  function buildPersonalStep() {
    const textarea = el("textarea", {
      id: "ob-instructions", rows: 3, maxlength: 4000,
      placeholder: "e.g. Be concise. I'm a developer who prefers Python and clear step-by-step reasoning.",
      value: draft.instructions,
    });
    textarea.addEventListener("input", () => { draft.instructions = textarea.value; });

    const presets = el("div", { className: "ob-presets" },
      ...INSTRUCTION_PRESETS.map(([label, text]) => {
        const chip = el("button", { type: "button", className: "ob-preset", text: "+ " + label });
        chip.addEventListener("click", () => {
          textarea.value = textarea.value.trim() ? textarea.value.trim() + " " + text : text;
          draft.instructions = textarea.value;
          textarea.focus();
        });
        return chip;
      }));

    const memInput = el("input", { type: "checkbox", checked: draft.memoryEnabled });
    memInput.addEventListener("change", () => { draft.memoryEnabled = memInput.checked; });
    const memRow = el("div", { className: "ob-toggle-row" },
      el("div", {}, el("strong", { text: "Memory" }), el("small", { text: "Remember facts you ask Pipkin to save." })),
      el("label", { className: "switch" }, memInput, el("span"), el("span", { className: "sr-only", text: "Memory" })));

    const webGroup = el("div", { className: "segmented", role: "radiogroup", "aria-label": "Web search" },
      ...[["auto", "Auto"], ["on", "On"], ["off", "Off"]].map(([value, label]) => {
        const input = el("input", { type: "radio", name: "ob-web", value, checked: draft.webMode === value });
        input.addEventListener("change", () => { draft.webMode = value; });
        return el("label", {}, input, el("span", { text: label }));
      }));
    const webRow = el("div", { className: "ob-toggle-row" },
      el("div", {}, el("strong", { text: "Web search" }), el("small", { text: "Auto searches only when current facts are needed." })),
      webGroup);

    const body = el("div", { className: "ob-stack" },
      el("div", { className: "ob-field" },
        el("label", { htmlFor: "ob-instructions" }, el("strong", { text: "Custom instructions" }), el("span", { className: "ob-opt", text: "Optional" })),
        textarea, presets),
      memRow,
      webRow);

    return stepFrame(
      "Make Pipkin yours",
      "Tailor how Pipkin responds to you. Skip anything you'd rather decide later.",
      body,
      { canSkip: true, onSkip: () => goTo(4, "next"), onNext: () => goTo(4, "next") },
    );
  }

  function buildReadyStep() {
    const info = host.getModels();
    const chosenModel = draft.model || info.model;
    const paletteName = (host.getPalettes?.() || []).find((p) => p.id === draft.palette)?.name;
    const chips = [
      (paletteName ? paletteName + " · " : "") + THEME_LABELS[draft.theme],
      MODE_LABELS[draft.appMode] + " mode",
      PERMISSION_LABELS[draft.permission],
      WEB_LABELS[draft.webMode],
      draft.memoryEnabled ? "Memory on" : "Memory off",
      chosenModel ? info.shortModelName(chosenModel) : null,
    ].filter(Boolean);

    const emblem = el("div", { className: "ob-emblem ob-emblem-ready", "aria-hidden": "true" },
      el("div", { className: "ob-ring ob-ring-1" }),
      el("div", { className: "ob-emblem-core ob-ready-core" }, icon("check")));
    const launch = el("button", { type: "button", className: "button accent ob-cta" },
      el("span", { text: "Start using Pipkin" }), icon("chevron-right"));
    launch.addEventListener("click", () => finish(true));
    const back = el("button", { type: "button", className: "ob-ghost", text: "← Adjust preferences" });
    back.addEventListener("click", () => goTo(3, "prev"));

    return el("div", { className: "ob-step ob-step-hero" },
      emblem,
      el("span", { className: "ob-eyebrow", text: "ALL SET" }),
      el("h1", { className: "ob-title", text: "You're ready to go" }),
      el("p", { className: "ob-lead", text: `Everything runs privately on ${plat().yourDevice}. You can change any of these later in Settings.` }),
      el("div", { className: "ob-pills" }, ...chips.map((text) => el("span", { className: "ob-pill", text }))),
      el("div", { className: "ob-hero-actions" }, launch, back));
  }

  function stepFrame(title, subtitle, content, { canSkip, onSkip, onNext }) {
    const back = el("button", { type: "button", className: "button", text: "Back" });
    back.addEventListener("click", () => goTo(step - 1, "prev"));
    const skip = canSkip ? el("button", { type: "button", className: "ob-skip-step", text: "Skip" }) : null;
    skip?.addEventListener("click", onSkip);
    const next = el("button", { type: "button", className: "button accent" },
      el("span", { text: "Continue" }), icon("chevron-right"));
    next.addEventListener("click", onNext);
    return el("div", { className: "ob-step" },
      el("div", { className: "ob-head" },
        el("h2", { className: "ob-heading", text: title }),
        el("p", { className: "ob-sub", text: subtitle })),
      el("div", { className: "ob-body" }, content),
      el("div", { className: "ob-footer" },
        back,
        el("div", { className: "ob-footer-right" }, skip, next)));
  }

  let stageTransitionTimer = 0;

  function renderStep(animateTransition = false) {
    if (!overlay) return;
    clearInterval(modelPoll);
    clearTimeout(stageTransitionTimer);
    const stage = overlay.querySelector(".ob-stage");
    const dots = overlay.querySelector(".ob-dots");
    const skipTop = overlay.querySelector(".ob-top-skip");
    if (skipTop) skipTop.hidden = step === 0 || step === TOTAL_STEPS - 1;
    dots.replaceChildren(...Array.from({ length: TOTAL_STEPS }, (_, idx) =>
      el("span", { className: "ob-dot", dataset: { state: idx === step ? "active" : idx < step ? "done" : "todo" } })));

    const builders = [buildWelcomeStep, buildThemeStep, buildWorkspaceStep, buildPersonalStep, buildReadyStep];
    const view = builders[step]();
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const prev = animateTransition && !reduced ? stage.querySelector(".ob-step:not(.ob-leaving)") : null;

    if (prev) {
      for (const stale of stage.querySelectorAll(".ob-step.ob-leaving")) stale.remove();
      const fromHeight = stage.offsetHeight;
      stage.style.height = fromHeight + "px";
      prev.classList.add("ob-leaving");
      prev.dataset.dir = direction;
      view.dataset.dir = direction;
      stage.append(view);
      const toHeight = view.offsetHeight;
      requestAnimationFrame(() => {
        stage.style.height = toHeight + "px";
        const target = view.querySelector("textarea, select, .ob-cta, .button.accent");
        target?.focus({ preventScroll: true });
      });
      stageTransitionTimer = setTimeout(() => {
        prev.remove();
        stage.style.height = "";
      }, 390);
    } else {
      stage.style.height = "";
      view.dataset.dir = direction;
      stage.replaceChildren(view);
      requestAnimationFrame(() => {
        const target = view.querySelector("textarea, select, .ob-cta, .button.accent");
        target?.focus({ preventScroll: true });
      });
    }
  }

  function goTo(nextStep, dir = "next") {
    const clamped = Math.max(0, Math.min(TOTAL_STEPS - 1, nextStep));
    if (clamped === step) return;
    step = clamped;
    direction = dir;
    renderStep(true);
  }

  let introTimers = [];
  function clearIntroTimers() {
    for (const id of introTimers) clearTimeout(id);
    introTimers = [];
  }

  function buildIntroStage() {
    const shards = CODE_SHARDS.map((text, index) => {
      const angle = (index / CODE_SHARDS.length) * Math.PI * 2 + (index % 2 ? 0.14 : -0.1);
      const radius = 28 + (index % 3) * 9; // vmin
      const sx = (Math.cos(angle) * radius).toFixed(1) + "vmin";
      const sy = (Math.sin(angle) * (radius * 0.72)).toFixed(1) + "vmin";
      const sr = ((index % 2 ? 1 : -1) * (8 + (index % 4) * 6)) + "deg";
      const delay = ((index * 24) % 240) + "ms";
      const node = el("span", { className: "ob-code-shard", dataset: { tone: index % 3 === 0 ? "accent" : index % 3 === 1 ? "cyan" : "muted" }, text });
      node.style.setProperty("--sx", sx);
      node.style.setProperty("--sy", sy);
      node.style.setProperty("--sr", sr);
      node.style.setProperty("--delay", delay);
      return node;
    });
    return el("div", { className: "ob-intro", "aria-hidden": "true" },
      ...shards,
      el("div", { className: "ob-shockwave" }),
      el("div", { className: "ob-shockwave ob-shockwave-2" }),
      el("div", { className: "ob-intro-logo" },
        el("img", { src: "/favicon.png", alt: "", width: 72, height: 72 })));
  }

  function finish(applyChanges) {
    if (!overlay) return;
    clearInterval(modelPoll);
    clearTimeout(stageTransitionTimer);
    clearIntroTimers();
    if (applyChanges && draft) host.applyPreferences(draft);
    markDone();
    if (location.pathname === "/welcome") {
      try { history.replaceState(null, "", "/"); } catch { /* ignore */ }
    }
    const closing = overlay;
    overlay = null;
    document.removeEventListener("keydown", onKey);
    closing.classList.add("ob-exit");
    const done = () => {
      closing.remove();
      document.body.removeAttribute("data-onboarding");
      host.focusComposer();
    };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) done();
    else setTimeout(done, 320);
  }

  function onKey(event) {
    if (!overlay) return;
    if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    }
  }

  function open({ preview = false } = {}) {
    if (overlay) return;
    previewMode = preview;
    const current = host.getSettings();
    const models = host.getModels();
    draft = {
      theme: current.theme || "system",
      palette: current.palette || "emerald",
      appMode: current.appMode || "chat",
      permission: current.permission || "ask",
      webMode: current.webMode || "auto",
      memoryEnabled: current.memoryEnabled ?? true,
      instructions: current.instructions || "",
      model: models.model || "",
    };
    step = 0;
    direction = "next";

    const skipTop = el("button", { type: "button", className: "ob-top-skip", text: previewMode ? "Close" : "Skip setup" });
    skipTop.addEventListener("click", () => finish(false));

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const intro = reduced ? null : buildIntroStage();

    overlay = el("div", {
      id: "onboarding", className: "onboarding", role: "dialog", "aria-modal": "true", "aria-label": "Welcome to Pipkin Desktop Harness",
    },
    el("div", { className: "ob-backdrop", "aria-hidden": "true" },
      el("div", { className: "ob-aura ob-aura-a" }),
      el("div", { className: "ob-aura ob-aura-b" }),
      el("div", { className: "ob-orbit ob-orbit-lg" }),
      el("div", { className: "ob-orbit ob-orbit-sm" })),
    intro,
    el("div", { className: "ob-card" },
      el("header", { className: "ob-top" },
        el("div", { className: "ob-dots", "aria-hidden": "true" }),
        skipTop),
      el("div", { className: "ob-stage" })));

    document.body.setAttribute("data-onboarding", "");
    document.body.append(overlay);
    document.addEventListener("keydown", onKey);
    renderStep();

    if (intro) {
      overlay.dataset.intro = "playing";
      const reveal = () => {
        if (!overlay || overlay.dataset.intro !== "playing") return;
        clearIntroTimers();
        overlay.dataset.intro = "reveal";
        introTimers.push(setTimeout(() => {
          intro.remove();
          if (overlay) overlay.dataset.intro = "done";
        }, 960));
      };
      intro.addEventListener("click", reveal, { once: true });
      introTimers.push(setTimeout(reveal, 1360));
    } else {
      overlay.dataset.intro = "none";
    }
  }

  window.PipkinOnboarding = { open };

  const shouldLaunch = location.pathname === "/welcome" ||
    (!localStorage.getItem(DONE_KEY) && host.isFirstRun());
  if (shouldLaunch) open({ preview: location.pathname === "/welcome" });
})();
