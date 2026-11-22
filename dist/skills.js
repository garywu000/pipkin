"use strict";

/* Slash commands and reusable instructions. Skill records contain text only. */
(() => {
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
    { name: "skills", icon: "book", description: "Browse and manage your recipes." },
    { name: "save-skill", icon: "plus", description: "Save a recipe from your latest completed Work run." },
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
    map_document: "Map source headings and anchors in one pass.",
    check_page: "Check the built page's local links and anchors.",
    run_command: "Run the relevant checks using the current approval settings.",
  };

  function parse(text) {
    const match = text.trim().match(/^\/([a-z][a-z0-9-]{0,31})(?:\s+([\s\S]*))?$/i);
    return match ? { name: match[1].toLowerCase(), input: match[2] || "" } : null;
  }

  function customSkill(value) {
    if (typeof value?.name !== "string" || typeof value.instructions !== "string") return null;
    const name = value.name.trim().replace(/^\/+/, "").toLowerCase();
    const instructions = value.instructions.trim();
    if (!names.test(name) || reserved.has(name) || !instructions || instructions.length > 4000) return null;
    const description = typeof value.description === "string" ? value.description.trim().slice(0, 120) : "";
    return { name, description, instructions };
  }

  function normalize(values) {
    const skills = new Map();
    for (const value of Array.isArray(values) ? values : []) {
      const skill = customSkill(value);
      if (skill) skills.set(skill.name, skill);
      if (skills.size >= 100) break;
    }
    return [...skills.values()];
  }

  function catalog(custom) {
    return [...commands, ...starters, ...custom];
  }

  function suggestions(text, custom) {
    const match = text.match(/^\/([a-z0-9-]*)$/i);
    return match ? catalog(custom).filter(({ name }) => name.startsWith(match[1].toLowerCase())) : [];
  }

  function mentionAt(text, caret) {
    const match = /(^|\s)@([a-z0-9-]*)$/i.exec(text.slice(0, caret));
    if (!match) return null;
    return { start: match.index + match[1].length, end: caret, query: match[2].toLowerCase() };
  }

  function requestedMentions(text) {
    const names = new Set([...text.matchAll(/(?:^|\s)@(github|gmail|doc)(?=$|[\s,:.!?])/gi)]
      .map((match) => match[1].toLowerCase()));
    return mentions.filter(({ name }) => names.has(name));
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

  function prompt(recipe, input) {
    return "Run the /" + recipe.name + " skill.\n\n" + recipe.instructions +
      (input ? "\n\nInput for this run\n" + input : "");
  }

  window.PipkinSkills = { starters, commands, mentions, mentionAt, requestedMentions,
    parse, customSkill, normalize, catalog, suggestions, fromRun, prompt };
})();
