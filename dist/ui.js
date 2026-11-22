"use strict";

/* Shared UI helpers: icons, toasts, and a DOM-only Markdown renderer (no innerHTML). */
(() => {
  const svgNS = "http://www.w3.org/2000/svg";

  /**
   * A loading spinner whose rotation follows a shared clock. Views that re-render while streaming
   * replace their spinners many times a second; without this each new one restarts at 0° and the
   * spinner visibly jumps.
   */
  function spinner(className = "spinner") {
    const node = document.createElement("span");
    node.className = className;
    node.setAttribute("aria-hidden", "true");
    if (typeof node.animate === "function") {
      // All spinners share the document timeline's origin, so a replacement continues at the same angle.
      node.style.animation = "none";
      const spin = node.animate([{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }],
        { duration: 750, iterations: Infinity, easing: "linear" });
      spin.startTime = 0;
    }
    return node;
  }

  function icon(name, className = "icon") {
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(svgNS, "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  }

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "className") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key === "style") node.style.cssText = value; // CSSOM works under the page's CSP; style attributes do not.
      else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key in node && typeof value !== "string") node[key] = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child !== null && child !== undefined && child !== false) node.append(child);
    }
    return node;
  }

  let toastTimer = 0;
  function toast(message) {
    const box = document.getElementById("toast");
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 1800);
  }

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      if (button) {
        const use = button.querySelector("use");
        const label = button.querySelector(".copy-label");
        const previous = use?.getAttribute("href");
        use?.setAttribute("href", "#i-check");
        if (label) label.textContent = "Copied";
        setTimeout(() => {
          if (previous) use.setAttribute("href", previous);
          if (label) label.textContent = "Copy";
        }, 1400);
      } else {
        toast("Copied to clipboard");
      }
      return true;
    } catch {
      toast("Select the text to copy it");
      return false;
    }
  }

  /* ---------------- syntax highlighting (lightweight) ---------------- */
  const KEYWORDS = new Set((
    "abstract as async await break case catch class const continue def default del delete do elif else enum export extends " +
    "false final finally fn for from func function go if impl import in interface is lambda let loop match mod mut new nil " +
    "none None not null or package pass private protected pub public raise return self static struct super switch this throw " +
    "throws true True False try type typeof undefined use var void while with yield echo then fi esac done local readonly " +
    "select where insert update into values create table drop alter join on group by order limit"
  ).split(" "));
  const PLAIN = new Set(["", "text", "plaintext", "txt", "markdown", "md", "output", "console", "log"]);
  const JSON_TOKEN = /("(?:\\.|[^"\\\n])*")(?=\s*:)|("(?:\\.|[^"\\\n])*")|(-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b/g;

  function highlightTokens(fragment, code, pattern, classify) {
    let cursor = 0;
    for (const match of code.matchAll(pattern)) {
      const cls = classify(match);
      if (!cls) continue;
      fragment.append(code.slice(cursor, match.index), el("span", { className: cls, text: match[0] }));
      cursor = match.index + match[0].length;
    }
    fragment.append(code.slice(cursor));
    return fragment;
  }

  function highlightDiff(fragment, code) {
    for (const [index, line] of code.split("\n").entries()) {
      if (index) fragment.append("\n");
      const meta = /^(?:@@|diff --git |index |--- |\+\+\+ |\\ No newline)/.test(line);
      const cls = meta ? "tok-f" : line.startsWith("+") ? "tok-diff-add" : line.startsWith("-") ? "tok-diff-del" : "";
      fragment.append(cls ? el("span", { className: cls, text: line }) : line);
    }
    return fragment;
  }

  function highlight(code, language) {
    const fragment = document.createDocumentFragment();
    if (PLAIN.has(language) || code.length > 60000) {
      fragment.append(code);
      return fragment;
    }
    if (language === "diff" || language === "patch") return highlightDiff(fragment, code);
    if (language === "json") {
      return highlightTokens(fragment, code, JSON_TOKEN, ([, key, string, number]) =>
        key ? "tok-f" : string ? "tok-s" : number ? "tok-n" : "tok-k");
    }
    const hashComments = /^(py|python|sh|bash|zsh|shell|ruby|rb|yaml|yml|toml|r|perl|dockerfile|make|makefile|ini|conf|properties)$/.test(language);
    const dashComments = /^(sql|lua|haskell|hs)$/.test(language);
    const pattern = new RegExp([
      String.raw`(\/\*[\s\S]*?\*\/|\/\/[^\n]*` + (hashComments ? String.raw`|#[^\n]*` : "") + (dashComments ? String.raw`|--[^\n]*` : "") + String.raw`|<!--[\s\S]*?-->)`,
      String.raw`("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|\x60(?:\\.|[^\x60\\])*\x60)`,
      String.raw`(\b(?:0x[\da-fA-F]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b)`,
      String.raw`([A-Za-z_$][\w$]*)(?=\s*\()`,
      String.raw`([A-Za-z_$][\w$]*)`,
    ].join("|"), "g");
    return highlightTokens(fragment, code, pattern, ([, comment, string, number, call, word]) =>
      comment ? "tok-c" : string ? "tok-s" : number ? "tok-n" : call && !KEYWORDS.has(call) ? "tok-f" : (call || word) && KEYWORDS.has(call || word) ? "tok-k" : "");
  }

  /* ---------------- math ---------------- */
  function renderMath(source, displayMode) {
    const holder = el("span", { className: displayMode ? "display-math" : "" });
    try {
      if (!window.katex || source.length > 6000) throw new Error();
      window.katex.render(source, holder, {
        displayMode, output: "mathml", trust: false, throwOnError: true, strict: "ignore", maxExpand: 100, maxSize: 24,
      });
      return holder;
    } catch {
      return null;
    }
  }

  /* ---------------- inline ---------------- */
  const INLINE = new RegExp([
    String.raw`(?<code>\x60+)(?<codeText>[\s\S]*?[^\x60])\k<code>(?!\x60)`,
    String.raw`\$\$(?<dmath>[^$]+?)\$\$`,
    String.raw`\\\((?<pmath>[\s\S]+?)\\\)`,
    String.raw`(?<![\\$\w])\$(?![\s$])(?<imath>(?:\\[^\n]|[^\\$\n])+?)(?<!\s)\$(?![\d$\w])`,
    String.raw`!?\[(?<linkText>[^\]\n]+)\]\((?<linkUrl><[^<>\n]+>|[^)\s]+)(?:\s+"[^"]*")?\)`,
    String.raw`<(?<angle>(?:https?|file):\/\/[^>\s]+)>`,
    String.raw`(?<auto>\b(?:https?|file):\/\/[^\s<>"'\x60]+[^\s<>"'\x60.,;:!?)\]])`,
    String.raw`\*\*(?=\S)(?<bold>[\s\S]+?)(?<=\S)\*\*`,
    String.raw`(?<![\w])__(?=\S)(?<bold2>[\s\S]+?)(?<=\S)__(?![\w])`,
    String.raw`~~(?=\S)(?<strike>[\s\S]+?)(?<=\S)~~`,
    String.raw`(?<![\w*])\*(?=[^\s*])(?<em>[^*\n]+?)(?<=[^\s*])\*(?![\w*])`,
    String.raw`(?<![\w])_(?=[^\s_])(?<em2>[^_\n]+?)(?<=[^\s_])_(?![\w])`,
    String.raw`(?<br>  \n|\\\n|\n)`,
  ].join("|"), "g");

  function safeUrl(url) {
    try {
      const parsed = new URL(url, location.href);
      return ["http:", "https:", "mailto:"].includes(parsed.protocol) ? parsed.href : null;
    } catch {
      return null;
    }
  }

  function link(text, href, literal = false) {
    if (href.startsWith("<") && href.endsWith(">")) href = href.slice(1, -1);
    try {
      const parsed = new URL(href, location.href);
      if (parsed.protocol === "file:" && !parsed.host && !parsed.username && !parsed.password) {
        return el("span", { text, dataset: { localPath: decodeURIComponent(parsed.pathname) } });
      }
      if (!/^(?:[a-z][a-z\d+.-]*:|\/\/|[#?])/i.test(href)) {
        const path = decodeURIComponent(href.split(/[?#]/, 1)[0]);
        return el("span", { text, dataset: { localPath: path } });
      }
    } catch {
      return document.createTextNode(text);
    }
    const url = safeUrl(href);
    if (!url) return document.createTextNode(text);
    const plain = literal || /^\s*(https?:\/\/|<https?:)/.test(text);
    return el("a", { href: url, target: "_blank", rel: "noopener noreferrer", title: url }, plain ? text : inline(text));
  }

  function inline(text) {
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    for (const match of text.matchAll(INLINE)) {
      const g = match.groups;
      fragment.append(text.slice(cursor, match.index));
      cursor = match.index + match[0].length;
      if (g.codeText !== undefined) {
        const value = g.codeText.length > 2 && g.codeText.startsWith(" ") && g.codeText.endsWith(" ") ? g.codeText.slice(1, -1) : g.codeText;
        fragment.append(el("code", { text: value }));
      } else if (g.dmath !== undefined || g.pmath !== undefined || g.imath !== undefined) {
        const math = renderMath(g.dmath ?? g.pmath ?? g.imath, g.dmath !== undefined);
        fragment.append(math || match[0]);
      } else if (g.linkText !== undefined) {
        fragment.append(link(g.linkText, g.linkUrl));
      } else if (g.angle !== undefined || g.auto !== undefined) {
        const url = g.angle ?? g.auto;
        fragment.append(link(url, url, true));
      } else if (g.bold !== undefined || g.bold2 !== undefined) {
        fragment.append(el("strong", {}, inline(g.bold ?? g.bold2)));
      } else if (g.strike !== undefined) {
        fragment.append(el("del", {}, inline(g.strike)));
      } else if (g.em !== undefined || g.em2 !== undefined) {
        fragment.append(el("em", {}, inline(g.em ?? g.em2)));
      } else if (g.br !== undefined) {
        fragment.append(el("br"));
      }
    }
    fragment.append(text.slice(cursor));
    return fragment;
  }

  /* ---------------- blocks ---------------- */
  const FENCE = /^ {0,3}(\x60{3,}|~{3,})\s*([\w+#.-]*)[^\n]*$/;
  const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
  const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const QUOTE = /^ {0,3}>\s?(.*)$/;

  function codeBlock(code, language) {
    const lang = (language || "").toLowerCase();
    const copy = el("button", { type: "button", "aria-label": "Copy code" }, icon("copy"), el("span", { className: "copy-label", text: "Copy" }));
    copy.addEventListener("click", () => copyText(code, copy));
    const codeEl = el("code");
    codeEl.append(highlight(code, lang));
    return el("div", { className: "code-block" },
      el("div", { className: "code-head" }, el("span", { text: lang || "code" }), copy),
      el("pre", {}, codeEl));
  }

  function splitRow(line) {
    let row = line.trim();
    if (row.startsWith("|")) row = row.slice(1);
    if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
    const cells = [];
    let current = "";
    let inCode = false;
    for (let i = 0; i < row.length; i++) {
      const ch = row[i];
      if (ch === "\\" && row[i + 1] === "|") { current += "|"; i++; continue; }
      if (ch === "\x60") inCode = !inCode;
      if (ch === "|" && !inCode) { cells.push(current.trim()); current = ""; continue; }
      current += ch;
    }
    cells.push(current.trim());
    return cells;
  }

  function table(lines) {
    const header = splitRow(lines[0]);
    const aligns = splitRow(lines[1]).map((cell) => cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "");
    const thead = el("thead", {}, el("tr", {}, header.map((cell, i) => el("th", { style: aligns[i] ? `text-align:${aligns[i]}` : null }, inline(cell)))));
    const tbody = el("tbody", {}, lines.slice(2).map((line) => {
      const cells = splitRow(line);
      return el("tr", {}, header.map((_, i) => el("td", { style: aligns[i] ? `text-align:${aligns[i]}` : null }, inline(cells[i] ?? ""))));
    }));
    return el("div", { className: "table-wrap" }, el("table", {}, thead, tbody));
  }

  function list(lines) {
    const stack = [];
    const itemsByList = new Map();
    let top = null;
    let lastItem = null;
    const make = (ordered, start) => {
      const node = el(ordered ? "ol" : "ul");
      if (ordered && start !== 1) node.start = start;
      itemsByList.set(node, []);
      return node;
    };
    for (const line of lines) {
      const match = line.match(LIST_ITEM);
      if (!match) {
        if (lastItem) lastItem.text.push(line.trim());
        continue;
      }
      const indent = match[1].replace(/\t/g, "    ").length;
      const ordered = /\d/.test(match[2]);
      const start = ordered ? parseInt(match[2], 10) : 1;
      while (stack.length > 1 && indent < stack.at(-1).indent) stack.pop();
      let level = stack.at(-1);
      if (!level) {
        level = { indent, element: make(ordered, start) };
        stack.push(level);
        top = level.element;
      } else if (indent >= level.indent + 2 && lastItem) {
        const nested = make(ordered, start);
        lastItem.nested.push(nested);
        level = { indent, element: nested };
        stack.push(level);
      }
      const item = { text: [match[3]], nested: [], li: el("li") };
      level.element.append(item.li);
      itemsByList.get(level.element).push(item);
      lastItem = item;
    }
    const fill = (listEl) => {
      for (const item of itemsByList.get(listEl) || []) {
        let text = item.text.join("\n");
        const task = text.match(/^\[( |x|X)\]\s+([\s\S]*)$/);
        if (task) {
          item.li.className = "task";
          item.li.append(el("input", { type: "checkbox", checked: task[1] !== " ", disabled: true }));
          text = task[2];
        }
        item.li.append(inline(text));
        for (const nested of item.nested) {
          item.li.append(nested);
          fill(nested);
        }
      }
    };
    if (top) fill(top);
    return top || document.createDocumentFragment();
  }

  function blocks(source) {
    const lines = source.replace(/\r\n?/g, "\n").split("\n");
    const out = document.createDocumentFragment();
    let i = 0;
    let paragraph = [];
    const flush = () => {
      if (paragraph.length) {
        out.append(el("p", {}, inline(paragraph.join("\n"))));
        paragraph = [];
      }
    };
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(FENCE);
      if (fence) {
        flush();
        const marker = fence[1];
        const body = [];
        i++;
        while (i < lines.length && !new RegExp("^ {0,3}" + marker[0] + "{" + marker.length + ",}\\s*$").test(lines[i])) {
          body.push(lines[i]);
          i++;
        }
        i++;
        out.append(codeBlock(body.join("\n"), fence[2]));
        continue;
      }
      if (!line.trim()) { flush(); i++; continue; }
      const trimmed = line.trim();
      if (trimmed.startsWith("$$") || trimmed.startsWith("\\[")) {
        const closer = trimmed.startsWith("$$") ? "$$" : "\\]";
        const single = trimmed.length > 2 && trimmed.endsWith(closer) && trimmed.length >= 4;
        const body = [];
        let j = i;
        if (single) {
          body.push(trimmed.slice(2, -2));
        } else {
          body.push(trimmed.slice(2));
          j++;
          while (j < lines.length && !lines[j].trim().endsWith(closer)) { body.push(lines[j]); j++; }
          if (j < lines.length) body.push(lines[j].trim().slice(0, -2));
        }
        if (single || j < lines.length) {
          const math = renderMath(body.join("\n").trim(), true);
          if (math) {
            flush();
            out.append(math);
            i = j + 1;
            continue;
          }
        }
      }
      const heading = line.match(HEADING);
      if (heading) {
        flush();
        out.append(el("h" + heading[1].length, {}, inline(heading[2])));
        i++;
        continue;
      }
      if (HR.test(line) && !paragraph.length) {
        out.append(el("hr"));
        i++;
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        flush();
        const rows = [line, lines[i + 1]];
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) { rows.push(lines[i]); i++; }
        out.append(table(rows));
        continue;
      }
      if (QUOTE.test(line)) {
        flush();
        const quoted = [];
        while (i < lines.length && QUOTE.test(lines[i])) {
          quoted.push(lines[i].match(QUOTE)[1]);
          i++;
        }
        const inner = el("blockquote");
        inner.append(blocks(quoted.join("\n")));
        out.append(inner);
        continue;
      }
      if (LIST_ITEM.test(line) && (!paragraph.length || /^\s*([-*+]|1[.)])\s/.test(line))) {
        flush();
        const items = [];
        while (i < lines.length) {
          const current = lines[i];
          if (FENCE.test(current) || HEADING.test(current)) break;
          if (!current.trim()) {
            const next = lines[i + 1];
            if (next !== undefined && (LIST_ITEM.test(next) || /^\s{2,}\S/.test(next)) && !FENCE.test(next)) { i++; continue; }
            break;
          }
          if (!LIST_ITEM.test(current) && !/^\s+\S/.test(current) && items.length && !lines[i - 1].trim()) break;
          if (HR.test(current) && !LIST_ITEM.test(current)) break;
          items.push(current);
          i++;
        }
        out.append(list(items));
        continue;
      }
      paragraph.push(line);
      i++;
    }
    flush();
    return out;
  }

  function renderMarkdown(target, text) {
    try {
      target.replaceChildren(blocks(text || ""));
    } catch (error) {
      console.error("Markdown rendering failed", error);
      target.textContent = text || "";
      target.style.whiteSpace = "pre-wrap";
    }
  }

  function plainText(text) {
    return (text || "").replace(/\x60{3}[^\n]*\n?/g, "").replace(/[*_~#>\x60|]/g, "").replace(/\[(.*?)\]\((.*?)\)/g, "$1").replace(/\s+/g, " ").trim();
  }

  function formatDuration(milliseconds, finished = true) {
    const seconds = Math.floor(milliseconds / 1000);
    if (!seconds) return finished ? "<1s" : "0s";
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds / 60) % 60;
    const remaining = seconds % 60;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${remaining}s`;
    return `${remaining}s`;
  }

  function formatSize(bytes) {
    if (!bytes) return "";
    const gb = bytes / 1024 ** 3;
    return gb >= 1 ? gb.toFixed(1) + " GB" : Math.round(bytes / 1024 ** 2) + " MB";
  }

  window.PipkinUI = { icon, spinner, el, toast, copyText, renderMarkdown, plainText, formatDuration, formatSize, inline };
})();
