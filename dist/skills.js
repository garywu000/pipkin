"use strict";

/* Slash commands, reusable workflows, shareable .pipkin-skill files, and safety inspection.
   Skill records contain text instructions and declarative checks only — never executable code. */
(() => {
  const SKILL_FORMAT = "pipkin-skill";
  const SKILL_PACK_FORMAT = "pipkin-skill-pack";
  const SKILL_FORMAT_VERSION = 1;

  const starters = [
    {
      name: "summarize", description: "Summarize an attachment, pasted text, or this chat.",
      instructions: "Summarize the supplied material with a short overview, key points, and open questions. Cite the file or section when available. If the material is missing, ask for it.",
    },
    {
      name: "extract-actions", description: "Find decisions, assignments, and deadlines in notes.",
      instructions: "Extract action items and decisions from the supplied notes. For each action, include the owner and due date if stated. Identify any owner, date, or next step that still needs confirmation.",
    },
    {
      name: "plan", description: "Turn a goal into a checklist with a finish line.",
      instructions: "Turn the goal into an ordered checklist with dependencies, verification steps, and a clear definition of done. Ask about missing details that would materially change the plan. Produce a plan for the user to review.",
    },
    {
      name: "review-changes", description: "Review a pasted or attached diff for issues.",
      instructions: "Review the supplied changes for correctness, regressions, and missing tests. Give each finding a file and line when available, explain the impact, and suggest a fix. Ask for a diff if the changes are unavailable.",
    },
    {
      name: "draft-email", description: "Write a draft with a subject and message.",
      instructions: "Draft an email with a subject and a concise message in the requested tone. Use placeholders for unknown names, dates, and details. Present the draft in the reply for the user to review.",
    },
    {
      name: "site", icon: "globe", description: "Build a local site from a brief or document in Work.",
      instructions: [
        "Build and verify a local site in the selected workspace. Keep source documents intact and follow the user's file and dependency constraints.",
        "Start with a checklist for mapping, building, and verification. Use the supplied source map. If more anchors are needed, request them together with map_document, then read only the ranges that fill gaps.",
        "Save a small, usable page once the key source anchors are known. Use write_page for a simple static page. For interactive work, write the structure and base styles with short unique section markers, then add data and behavior one section at a time. Keep the page usable after each edit.",
        "Keep each edit independently reviewable. Use relative links to the supplied materials and verified page numbers. Flag missing or conflicting facts.",
        "Each HTML edit checks local links and anchors. Run check_page again if a later change adds an asset. Verify interactive behavior if you have a local preview, and state which checks ran. Report the output path and any unresolved details.",
      ].join("\n"),
    },
    {
      name: "study-guide", icon: "book", description: "Turn a PDF packet, syllabus, or notes into a page-linked study guide.",
      instructions: "Build a navigable HTML study guide from the supplied document or brief. Organize key concepts, definitions, and review questions into clear sections, and link every section to its supporting source page.",
    },
    {
      name: "checklist", icon: "list", description: "Turn a brief, syllabus, or packet into a page-linked action checklist.",
      instructions: "Build an interactive HTML action checklist from the supplied document or brief. List every concrete task, requirement, and deadline, and link every section to its supporting source page.",
    },
    {
      name: "word", icon: "file", description: "Write an editable Word document in the selected Work folder.",
      instructions: "Create a structured Word document from the brief or attachments. Keep headings descriptive and preserve source facts.",
    },
    {
      name: "powerpoint", icon: "file", description: "Build an editable PowerPoint deck in the selected Work folder.",
      instructions: "Build a PowerPoint deck with a title slide and one main idea per slide. Keep slide text concise and cite supplied sources.",
    },
    {
      name: "pdf", icon: "file", description: "Write a paginated PDF in the selected Work folder.",
      instructions: "Create a readable PDF from the supplied material with a clear title, headings, source notes and page numbers.",
    },
    {
      name: "humanize", icon: "note", description: "Cut stiff phrasing and keep your voice. Tones include --warm, --direct, --professional, and --academic.",
      instructions: "Make light edits where phrasing feels stiff. Keep the writer's voice, meaning, and details.",
    },
    {
      name: "humanize-paper", icon: "book", description: "Make a paper or essay read more naturally while preserving its argument and citations.",
      instructions: "Edit a paper or essay for clarity and flow while preserving its evidence and claims.",
    },
  ];
  const commands = [
    { name: "compact", icon: "book", description: "Summarize older turns to free space in this chat." },
    { name: "goal", icon: "list", description: "Set what this chat should work toward." },
    { name: "model", icon: "sparkle", description: "Choose a local model and context size." },
    { name: "init", icon: "file", description: "Draft instructions for the selected workspace." },
    { name: "pin", icon: "pin", description: "Pin or unpin this chat." },
    { name: "delete-chat", icon: "trash", description: "Open the delete confirmation for this chat." },
    { name: "pet", icon: "bot", description: "Show or hide Sprig on your desktop." },
    { name: "skills", icon: "book", description: "Browse, import, and share your skills." },
    { name: "save-skill", icon: "plus", description: "Save a skill from your latest completed Work run." },
    { name: "schedule", icon: "clock", description: "View scheduled tasks and background inbox." },
  ];
  const mentions = [
    { name: "github", icon: "globe", description: "Public GitHub links in Work", instructions:
      "Use the public GitHub link the user supplies for this task. Ask for the link when it is missing. Cite the public page and report which information you can verify." },
    { name: "gmail", icon: "note", description: "Draft a message locally", instructions:
      "Prepare a Gmail draft with a subject, body, and any recipients the user supplied. Present it for review in chat." },
    { name: "doc", icon: "file", description: "Use an attached document", instructions:
      "Use the attached document or pasted excerpt. Cite its section or filename. Ask the user to attach the document if none is available." },
  ];
  const reserved = new Set([...starters, ...commands].map(({ name }) => name));
  const workflows = { __proto__: null, site: "site", "study-guide": "study-guide", checklist: "checklist", word: "word", powerpoint: "slides", pdf: "pdf" };
  const allowedIcons = new Set(["sparkle", "book", "list", "file", "note", "globe", "bot", "pin", "folder", "shield", "clock"]);
  const allowedOutputs = new Set(["", "chat", "markdown", "site", "study-guide", "checklist", "word", "slides", "pdf"]);
  const workflowOutputs = new Set(["site", "study-guide", "checklist", "word", "slides", "pdf"]);
  const checkTypes = new Set(["file_exists", "html_links", "contains_heading", "contains_text", "min_words", "citations", "no_placeholders"]);
  const outputLabels = {
    "": "Flexible (Chat or Work)",
    chat: "Chat reply",
    markdown: "Markdown file",
    site: "Local site (HTML)",
    "study-guide": "Study guide (HTML)",
    checklist: "Action checklist (HTML)",
    word: "Word document (.docx)",
    slides: "PowerPoint deck (.pptx)",
    pdf: "PDF document (.pdf)",
  };
  const deliverables = {
    site: /\b(?:web\s?page|web\s?site|site|landing page|html page)\b|\.html?(?=\W|$)/i,
    "study-guide": /\bstudy\s+guide\b/i,
    checklist: /\b(?:action\s+)?checklist\b/i,
    word: /\b(?:microsoft word|word (?:doc(?:ument)?|report)|(?:in|as) word|docx)\b|\.docx(?=\W|$)/i,
    slides: /\b(?:powerpoint|pptx|slide deck|slides?|presentation)\b|\.pptx(?=\W|$)/i,
    pdf: /\bpdf\b|\.pdf(?=\W|$)/i,
  };

  function detectWorkflow(text) {
    const request = text.trimStart().replace(
      /^(?:(?:please|can you|could you|would you|help me|i want you to|i['’]d like you to)\s+)+/i, "",
    ).replace(/^(?:using|with|based on)\s+[^,\n]{1,220},\s*/i, "");
    const action = request.match(/^(build|create|make|generate|write|draft|prepare|design|turn|convert|export|save|assemble|produce)\s+(.+)/i);
    if (!action) return null;
    let target = action[2].slice(0, 450);
    if (/^(?:turn|convert)$/i.test(action[1])) {
      target = target.match(/\b(?:into|as|to)\s+(.+)/i)?.[1] || "";
    } else {
      const destination = target.match(/\b(?:save|export)(?:\s+(?:it|this))?\s+(?:as|to)\s+(.+)/i);
      const subject = target.split(/\s+(?:from|using|based on|of|about|for|with links? to)\s+/i, 1)[0];
      target = destination ? subject + " " + destination[1] : subject;
    }
    const kinds = Object.keys(deliverables).filter((key) => deliverables[key].test(target));
    return kinds.length === 1 ? kinds[0] : null;
  }

  const names = /^[a-z][a-z0-9-]{0,31}$/;
  const stepDescriptions = {
    list_files: "Locate the relevant files.",
    outline_file: "Identify the relevant sections or definitions.",
    search_text: "Search for the relevant terms and references.",
    read_file: "Read the relevant sections and record citations.",
    read_files: "Read related file ranges together and record citations.",
    web_search: "Find pertinent sources.",
    web_fetch: "Read the most pertinent sources and record their URLs.",
    edit_file: "Prepare the necessary edits using the current approval settings.",
    write_file: "Prepare the necessary files using the current approval settings.",
    write_page: "Build a page from the supplied content using the current approval settings.",
    write_artifact: "Render and verify the requested file before saving it.",
    map_document: "Map source headings and anchors in one pass.",
    check_page: "Check the built page's local links and anchors.",
    run_command: "Run the relevant checks using the current approval settings.",
  };

  const securityRules = [
    {
      kind: "prompt_override",
      label: "Tries to override instructions or safety settings",
      pattern: /\b(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?|disregard\s+(?:the\s+)?(?:system|safety|rules)|bypass\s+(?:the\s+)?(?:approval|sandbox|safety|check)|disable\s+(?:approval|safety|protection|guard)|override\s+(?:system\s+prompt|safety|permissions?)|auto[- ]?approve|without\s+asking\s+(?:the\s+user|permission))\b/i,
    },
    {
      kind: "shell_command",
      label: "Mentions shell commands or script execution",
      pattern: /(?:\brun_command\b|\bterminal_run\b|\bosascript\b|\blaunchctl\b|\brm\s+-rf\b|\bsudo\b|\b(?:bash|sh|zsh|powershell|cmd\.exe)\s+-c\b|\b(?:curl|wget)\s+https?:\/\/|\b(?:eval|exec)\s*\()/i,
    },
    {
      kind: "external_network",
      label: "References external URLs or sending data out",
      pattern: /(?:https?:\/\/[^\s)"'>]{4,}|\b(?:webhook|exfiltrat\w*|upload\s+to|post\s+to\s+https?)\b)/i,
    },
    {
      kind: "sensitive_paths",
      label: "Mentions credentials, keys, or sensitive local paths",
      pattern: /(?:~\/\.ssh|\b\.ssh\/|\bid_rsa\b|\bid_ed25519\b|\b\.env(?:\.local)?\b|\bkeychain\b|\/etc\/passwd|\bprivate\s+key\b|\bapi[_ ]?key\b|\baccess[_ ]?token\b|\bsecret[_ ]?key\b)/i,
    },
  ];

  function cleanText(value, limit) {
    return typeof value === "string" ? value.replace(/\x00/g, "").trim().slice(0, limit) : "";
  }

  function defaultCheckLabel(check) {
    switch (check.type) {
      case "file_exists":
        return check.pattern && check.pattern !== "*"
          ? `Saves a matching file (${check.pattern})`
          : "Saves at least one output file";
      case "html_links": return "Checks local HTML links and anchors";
      case "contains_heading": return `Includes heading “${check.text}”`;
      case "contains_text": return `Includes “${check.text}”`;
      case "min_words": return `At least ${check.count} words`;
      case "citations": return "Includes source citations or links";
      case "no_placeholders": return "No TODO / TBD placeholders left";
      default: return check.type;
    }
  }

  function parseCheckShorthand(token) {
    const text = String(token || "").trim();
    const match = text.match(/^([a-z_]+)(?:\((.*)\))?$/i);
    if (!match) return null;
    const type = match[1].toLowerCase();
    const arg = (match[2] || "").trim().replace(/^["']|["']$/g, "");
    if (type === "file_exists") return { type, pattern: arg || "*" };
    if (type === "html_links" || type === "citations" || type === "no_placeholders") return { type };
    if ((type === "contains_heading" || type === "contains_text") && arg) return { type, text: arg };
    if (type === "min_words" && arg) return { type, count: Number(arg) };
    return null;
  }

  function normalizeCheck(item) {
    if (typeof item === "string") item = parseCheckShorthand(item);
    if (!item || typeof item !== "object") return null;
    const type = String(item.type || "").trim().toLowerCase();
    if (!checkTypes.has(type)) return null;
    const out = { type };
    if (type === "file_exists") {
      let pattern = cleanText(item.pattern || "*", 80).replace(/\\/g, "/");
      if (!pattern || pattern.includes("..") || pattern.startsWith("/") || pattern.includes("\n")) pattern = "*";
      out.pattern = pattern;
    } else if (type === "contains_heading" || type === "contains_text") {
      const text = cleanText(item.text ?? item.value ?? "", 80).replace(/\n/g, " ");
      if (!text) return null;
      out.text = text;
    } else if (type === "min_words") {
      const count = Number.parseInt(item.count ?? item.value, 10);
      if (!Number.isFinite(count) || count < 10 || count > 20000) return null;
      out.count = count;
    }
    out.label = cleanText(item.label || "", 80) || defaultCheckLabel(out);
    return out;
  }

  function normalizeChecks(items) {
    if (!Array.isArray(items)) return [];
    const out = [];
    const seen = new Set();
    for (const item of items) {
      const clean = normalizeCheck(item);
      if (!clean) continue;
      const key = `${clean.type}|${clean.pattern || ""}|${(clean.text || "").toLowerCase()}|${clean.count || 0}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(clean);
      if (out.length >= 8) break;
    }
    return out;
  }

  function normalizeInputs(items) {
    if (!Array.isArray(items)) return [];
    const out = [];
    const seen = new Set();
    for (const item of items) {
      if (!item || typeof item !== "object") continue;
      const name = cleanText(item.name || "", 32).toLowerCase().replace(/[^a-z0-9_-]/g, "");
      if (!name || seen.has(name)) continue;
      seen.add(name);
      const label = cleanText(item.label || name.replace(/[-_]+/g, " "), 60);
      const placeholder = cleanText(item.placeholder || "", 100);
      out.push({ name, label, placeholder, required: Boolean(item.required) });
      if (out.length >= 5) break;
    }
    return out;
  }

  function normalizePermissions(value, output = "") {
    const raw = value && typeof value === "object" ? value : {};
    const impliesWrites = workflowOutputs.has(output) || output === "markdown";
    const writes = Boolean(raw.writes ?? impliesWrites);
    const workspace = Boolean(raw.workspace ?? writes);
    const web = Boolean(raw.web ?? false);
    return { workspace, writes, web };
  }

  function parse(text) {
    const match = text.trim().match(/^\/([a-z][a-z0-9-]{0,31})(?:\s+([\s\S]*))?$/i);
    return match ? { name: match[1].toLowerCase(), input: match[2] || "" } : null;
  }

  function customSkill(value, { allowWorkspaceMeta = false } = {}) {
    if (typeof value?.name !== "string" || typeof value.instructions !== "string") return null;
    const name = value.name.trim().replace(/^\/+/, "").toLowerCase();
    const instructions = cleanText(value.instructions, 4001);
    if (!names.test(name) || reserved.has(name) || !instructions || instructions.length > 4000) return null;
    const description = cleanText(value.description || "", 120);
    const author = cleanText(value.author || "", 60);
    const iconRaw = cleanText(value.icon || "", 24).toLowerCase();
    const icon = allowedIcons.has(iconRaw) ? iconRaw : "sparkle";
    const outputRaw = cleanText(value.output || "", 24).toLowerCase();
    const output = allowedOutputs.has(outputRaw) ? outputRaw : "";
    const inputs = normalizeInputs(value.inputs);
    const checks = normalizeChecks(value.checks);
    const permissions = normalizePermissions(value.permissions, output);

    const skill = { name, description, instructions };
    if (author) skill.author = author;
    if (icon !== "sparkle") skill.icon = icon;
    if (output) skill.output = output;
    if (inputs.length) skill.inputs = inputs;
    if (checks.length) skill.checks = checks;
    if (permissions.workspace || permissions.writes || permissions.web || (value.permissions && typeof value.permissions === "object")) {
      skill.permissions = permissions;
    }
    if (allowWorkspaceMeta && value.source === "workspace") {
      skill.source = "workspace";
      if (typeof value.file === "string") skill.file = value.file.slice(0, 160);
      if (value.security && typeof value.security === "object") skill.security = value.security;
    }
    return skill;
  }

  function normalize(values, options = {}) {
    const skills = new Map();
    const limit = options.allowWorkspaceMeta ? 30 : 100;
    for (const value of Array.isArray(values) ? values : []) {
      const skill = customSkill(value, options);
      if (skill) skills.set(skill.name, skill);
      if (skills.size >= limit) break;
    }
    return [...skills.values()];
  }

  function mergeCustomAndWorkspace(custom, workspaceSkills = []) {
    const personalNames = new Set((custom || []).map(({ name }) => name));
    const activeWorkspace = (workspaceSkills || []).filter(({ name }) => !personalNames.has(name));
    return [...(custom || []), ...activeWorkspace];
  }

  function catalog(custom, workspaceSkills = []) {
    return [...commands, ...starters, ...mergeCustomAndWorkspace(custom, workspaceSkills)];
  }

  function suggestions(text, custom, workspaceSkills = []) {
    const match = text.match(/^\/([a-z0-9-]*)$/i);
    return match ? catalog(custom, workspaceSkills).filter(({ name }) => name.startsWith(match[1].toLowerCase())) : [];
  }

  function mentionAt(text, caret) {
    const match = /(^|\s)@([a-z0-9-]*)$/i.exec(text.slice(0, caret));
    if (!match) return null;
    return { start: match.index + match[1].length, end: caret, query: match[2].toLowerCase() };
  }

  function requestedMentions(text) {
    const found = new Set([...text.matchAll(/(?:^|\s)@(github|gmail|doc)(?=$|[\s,:.!?])/gi)]
      .map((match) => match[1].toLowerCase()));
    return mentions.filter(({ name }) => found.has(name));
  }

  function fromRun(run) {
    if (run?.state !== "complete") return "";
    const plan = (Array.isArray(run.plan) ? run.plan : [])
      .filter((step) => step?.status === "completed" && typeof step.text === "string").map(({ text }) => text);
    const observed = (Array.isArray(run.entries) ? run.entries : []).filter((entry) => entry?.type === "tool" && entry.state === "ok")
      .map((entry) => stepDescriptions[entry.name]).filter(Boolean);
    const steps = [...new Set(plan.length ? plan : observed)].slice(0, 10);
    return steps.length ? steps.map((step, index) => (index + 1) + ". " + step).join("\n") : "";
  }

  function checksFromRun(run) {
    const files = [...(run?.files || []), ...(run?.outputs || []).map((o) => o?.path).filter(Boolean)];
    const suggested = [];
    if (files.length) {
      const exts = [...new Set(files.map((p) => {
        const idx = p.lastIndexOf(".");
        return idx > 0 ? p.slice(idx).toLowerCase() : "";
      }).filter(Boolean))];
      suggested.push({ type: "file_exists", pattern: exts.length === 1 ? "*" + exts[0] : "*" });
      if (exts.some((ext) => ext === ".html" || ext === ".htm")) {
        suggested.push({ type: "html_links" });
      }
      suggested.push({ type: "no_placeholders" });
    }
    return normalizeChecks(suggested);
  }

  function prompt(recipe, input) {
    const parts = ["Run the /" + recipe.name + " skill.", recipe.instructions];
    if (recipe.inputs?.length) {
      parts.push("Expected inputs:\n" + recipe.inputs.map((item) =>
        `- ${item.label}${item.required ? " (required)" : ""}${item.placeholder ? `: ${item.placeholder}` : ""}`,
      ).join("\n"));
    }
    if (recipe.checks?.length) {
      parts.push("Verification checks for this skill:\n" + recipe.checks.map((c) => "- " + c.label).join("\n"));
    }
    if (input) parts.push("Input for this run\n" + input);
    return parts.join("\n\n");
  }

  function inspectSecurity(skill) {
    const parts = [skill?.description || "", skill?.instructions || ""];
    for (const item of skill?.inputs || []) parts.push(`${item.label || ""} ${item.placeholder || ""}`);
    for (const check of skill?.checks || []) parts.push(`${check.pattern || ""} ${check.text || ""} ${check.label || ""}`);
    const haystack = parts.join("\n");
    const flags = [];
    for (const rule of securityRules) {
      const match = rule.pattern.exec(haystack);
      if (match) {
        const start = Math.max(0, match.index - 28);
        const end = Math.min(haystack.length, match.index + match[0].length + 36);
        flags.push({
          kind: rule.kind,
          label: rule.label,
          excerpt: haystack.slice(start, end).replace(/\s+/g, " ").trim(),
        });
      }
    }
    const level = flags.length ? "review" : "standard";
    const summary = flags.length
      ? `Review ${flags.length} flagged ${flags.length === 1 ? "pattern" : "patterns"} before installing or running.`
      : "Text instructions and local checks only. Cannot run code or change your approval settings.";
    return { level, flags, summary };
  }

  function slugFromFilename(filename) {
    let stem = String(filename || "").split(/[\\/]/).pop() || "";
    for (const suffix of [".pipkin-skill.md", ".pipkin-skill-pack", ".pipkin-skill", ".json", ".md"]) {
      if (stem.toLowerCase().endsWith(suffix)) {
        stem = stem.slice(0, -suffix.length);
        break;
      }
    }
    return stem.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").replace(/-{2,}/g, "-").slice(0, 32);
  }

  function parseMarkdownSkill(text, filename = "") {
    const cleaned = String(text || "").replace(/\r\n/g, "\n").replace(/^\ufeff/, "");
    const meta = {};
    let body = cleaned;
    if (cleaned.startsWith("---\n")) {
      const end = cleaned.indexOf("\n---", 4);
      if (end !== -1) {
        const fm = cleaned.slice(4, end);
        body = cleaned.slice(end + 4).replace(/^\n+/, "");
        let listKey = null;
        for (const line of fm.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith("#")) continue;
          if (trimmed.startsWith("- ") && listKey) {
            (meta[listKey] ||= []).push(trimmed.slice(2).trim());
            continue;
          }
          listKey = null;
          const colon = line.indexOf(":");
          if (colon > 0) {
            const key = line.slice(0, colon).trim().toLowerCase();
            const val = line.slice(colon + 1).trim();
            if (!val) {
              listKey = key;
              meta[key] ||= [];
            } else {
              meta[key] = val.replace(/^["']|["']$/g, "");
            }
          }
        }
      }
    }
    let checks = [];
    if (Array.isArray(meta.checks)) checks = meta.checks;
    else if (typeof meta.checks === "string") {
      checks = meta.checks.split(/,(?![^()]*\))/).map((s) => s.trim()).filter(Boolean);
    }
    const permissions = {};
    for (const key of ["workspace", "writes", "web"]) {
      if (typeof meta[key] === "string") permissions[key] = /^(?:1|true|yes|on)$/i.test(meta[key].trim());
    }
    const raw = {
      name: meta.name || slugFromFilename(filename),
      description: meta.description || "",
      author: meta.author || "",
      icon: meta.icon || "sparkle",
      output: meta.output || "",
      instructions: body.trim(),
      checks,
      ...(Object.keys(permissions).length ? { permissions } : {}),
    };
    const valid = customSkill(raw);
    if (!valid) {
      const candidate = cleanText(raw.name || "", 33).replace(/^\/+/, "").toLowerCase();
      if (reserved.has(candidate)) throw new Error(`/${candidate} is a built-in Pipkin command and cannot be overwritten.`);
      throw new Error("Could not validate this Markdown skill (check slash name and instructions).");
    }
    return valid;
  }

  function parseSkillFile(rawText, filename = "") {
    if (typeof rawText !== "string" || !rawText.trim()) throw new Error("This skill file is empty.");
    const trimmed = rawText.replace(/^\ufeff/, "").trim();
    const skills = [];
    const warnings = [];
    let isPack = false;

    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      let data;
      try {
        data = JSON.parse(trimmed);
      } catch {
        throw new Error("Could not read JSON in this skill file.");
      }
      if (data && typeof data === "object" && (data.format === SKILL_PACK_FORMAT || Array.isArray(data.skills))) {
        isPack = true;
        const entries = Array.isArray(data.skills) ? data.skills : [];
        if (!entries.length) throw new Error("This skill pack contains no skills.");
        for (const [index, entry] of entries.slice(0, 50).entries()) {
          const valid = customSkill(entry);
          if (valid) skills.push(valid);
          else warnings.push(`Skipped skill #${index + 1} (invalid or reserved name).`);
        }
      } else if (Array.isArray(data)) {
        isPack = true;
        for (const [index, entry] of data.slice(0, 50).entries()) {
          const valid = customSkill(entry);
          if (valid) skills.push(valid);
          else warnings.push(`Skipped skill #${index + 1} (invalid or reserved name).`);
        }
      } else if (data && typeof data === "object") {
        if (data.format && data.format !== SKILL_FORMAT) {
          throw new Error("This file is not a .pipkin-skill file.");
        }
        const valid = customSkill(data);
        if (!valid) {
          const candidate = cleanText(data.name || "", 33).replace(/^\/+/, "").toLowerCase();
          if (reserved.has(candidate)) throw new Error(`/${candidate} is a built-in Pipkin command and cannot be overwritten.`);
          throw new Error("This skill file is missing a valid slash name or instructions.");
        }
        skills.push(valid);
      } else {
        throw new Error("Unsupported skill file format.");
      }
    } else {
      skills.push(parseMarkdownSkill(rawText, filename));
    }

    if (!skills.length) throw new Error(warnings[0] || "No valid skills found in this file.");
    return { skills, warnings, isPack };
  }

  function serializeSkill(skill) {
    const clean = customSkill(skill);
    if (!clean) throw new Error("Cannot export an invalid skill.");
    return JSON.stringify({ format: SKILL_FORMAT, version: SKILL_FORMAT_VERSION, ...clean }, null, 2) + "\n";
  }

  function serializeSkillPack(skills, name = "Pipkin Skill Pack") {
    const clean = normalize(skills);
    return JSON.stringify({
      format: SKILL_PACK_FORMAT,
      version: SKILL_FORMAT_VERSION,
      name,
      exportedAt: new Date().toISOString(),
      skills: clean,
    }, null, 2) + "\n";
  }

  function isSkillFilename(filename) {
    const lower = String(filename || "").toLowerCase();
    return lower.endsWith(".pipkin-skill") || lower.endsWith(".pipkin-skill-pack") || lower.endsWith(".pipkin-skill.md");
  }

  window.PipkinSkills = {
    SKILL_FORMAT, SKILL_PACK_FORMAT, SKILL_FORMAT_VERSION,
    starters, commands, mentions, workflows, workflowOutputs, outputLabels, allowedOutputs,
    detectWorkflow, mentionAt, requestedMentions,
    parse, customSkill, normalize, normalizeCheck, normalizeChecks, normalizeInputs, normalizePermissions,
    mergeCustomAndWorkspace, catalog, suggestions, fromRun, checksFromRun, prompt,
    inspectSecurity, parseSkillFile, serializeSkill, serializeSkillPack, isSkillFilename,
  };
})();
