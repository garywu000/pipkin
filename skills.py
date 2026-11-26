"""Shareable Pipkin skills (.pipkin-skill), workspace packs (.pipkin/skills/),
security inspection, and deterministic post-run verification checks.

Skills are pure data (instructions + optional declarative checks). They never
execute arbitrary code and never elevate workspace, shell, or web permissions.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    import agent

SKILL_FORMAT = "pipkin-skill"
SKILL_PACK_FORMAT = "pipkin-skill-pack"
SKILL_FORMAT_VERSION = 1

MAX_SKILL_FILE_BYTES = 32 * 1024
MAX_PACK_FILE_BYTES = 512 * 1024
MAX_WORKSPACE_SKILLS = 30
MAX_SKILLS_IN_PACK = 50
MAX_CHECKS = 8
MAX_INPUTS = 5
MAX_NAME = 32
MAX_DESCRIPTION = 120
MAX_AUTHOR = 60
MAX_INSTRUCTIONS = 4000
MAX_LABEL = 80
MAX_PATTERN = 80

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

RESERVED_NAMES = frozenset({
    # Built-in commands
    "compact", "goal", "model", "init", "pin", "delete-chat", "pet", "skills", "save-skill", "schedule",
    # Built-in starter recipes
    "summarize", "extract-actions", "plan", "review-changes", "draft-email",
    "site", "study-guide", "checklist", "word", "powerpoint", "pdf",
    "humanize", "humanize-paper",
})

ALLOWED_ICONS = frozenset({
    "sparkle", "book", "list", "file", "note", "globe", "bot", "pin", "folder", "shield", "clock",
})

ALLOWED_OUTPUTS = frozenset({
    "", "chat", "markdown", "site", "study-guide", "checklist", "word", "slides", "pdf",
})

WORKFLOW_OUTPUTS = frozenset({"site", "study-guide", "checklist", "word", "slides", "pdf"})

CHECK_TYPES = frozenset({
    "file_exists",
    "html_links",
    "contains_heading",
    "contains_text",
    "min_words",
    "citations",
    "no_placeholders",
})

WORD_TOKEN_RE = re.compile(r"[^\s\u2013\u2014]+")
WORD_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
HTML_HEADING_RE = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)
MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
CITATION_RE = re.compile(
    r"(?:\[(?:S|A|F|W)\d+\]|\[[^\]]{1,80}\]\([^)\s]{1,240}\)|\b(?:p|pp)\.?\s*\d+|\bpage\s+\d+\b|https?://[^\s)\]]+)",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"(?:\b(?:TODO|TBD|FIXME|XXX)\b|\[(?:insert|add|your|fill\s+in|placeholder|replace)[^\]]{0,60}\]|\blorem\s+ipsum\b)",
    re.IGNORECASE,
)

SECURITY_RULES = (
    (
        "prompt_override",
        "Tries to override instructions or safety settings",
        re.compile(
            r"\b(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?"
            r"|disregard\s+(?:the\s+)?(?:system|safety|rules)"
            r"|bypass\s+(?:the\s+)?(?:approval|sandbox|safety|check)"
            r"|disable\s+(?:approval|safety|protection|guard)"
            r"|override\s+(?:system\s+prompt|safety|permissions?)"
            r"|auto[- ]?approve|without\s+asking\s+(?:the\s+user|permission))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "shell_command",
        "Mentions shell commands or script execution",
        re.compile(
            r"(?:\brun_command\b|\bterminal_run\b|\bosascript\b|\blaunchctl\b|\brm\s+-rf\b|\bsudo\b"
            r"|\b(?:bash|sh|zsh|powershell|cmd\.exe)\s+-c\b|\b(?:curl|wget)\s+https?://|\b(?:eval|exec)\s*\()",
            re.IGNORECASE,
        ),
    ),
    (
        "external_network",
        "References external URLs or sending data out",
        re.compile(
            r"(?:https?://[^\s)\"'>]{4,}|\b(?:webhook|exfiltrat\w*|upload\s+to|post\s+to\s+https?)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "sensitive_paths",
        "Mentions credentials, keys, or sensitive local paths",
        re.compile(
            r"(?:~/\.ssh|\b\.ssh/|\bid_rsa\b|\bid_ed25519\b|\b\.env(?:\.local)?\b|\bkeychain\b"
            r"|/etc/passwd|\bprivate\s+key\b|\bapi[_ ]?key\b|\baccess[_ ]?token\b|\bsecret[_ ]?key\b)",
            re.IGNORECASE,
        ),
    ),
)


def _clean_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", "").strip()[:limit]


def _default_check_label(check: dict) -> str:
    ctype = check["type"]
    if ctype == "file_exists":
        pat = check.get("pattern") or "*"
        return f"Saves a matching file ({pat})" if pat != "*" else "Saves at least one output file"
    if ctype == "html_links":
        return "Checks local HTML links and anchors"
    if ctype == "contains_heading":
        return f"Includes heading “{check['text']}”"
    if ctype == "contains_text":
        return f"Includes “{check['text']}”"
    if ctype == "min_words":
        return f"At least {check['count']} words"
    if ctype == "citations":
        return "Includes source citations or links"
    if ctype == "no_placeholders":
        return "No TODO / TBD placeholders left"
    return ctype


def normalize_check(item) -> Optional[dict]:
    if isinstance(item, str):
        parsed = _parse_check_shorthand(item)
        if parsed is None:
            return None
        item = parsed
    if not isinstance(item, dict):
        return None
    ctype = str(item.get("type") or "").strip().lower()
    if ctype not in CHECK_TYPES:
        return None
    out = {"type": ctype}
    if ctype == "file_exists":
        pattern = _clean_text(item.get("pattern") or "*", MAX_PATTERN).replace("\\", "/")
        if not pattern or ".." in pattern or pattern.startswith("/") or "\n" in pattern:
            pattern = "*"
        out["pattern"] = pattern
    elif ctype in ("contains_heading", "contains_text"):
        text = _clean_text(item.get("text") or item.get("value") or "", MAX_PATTERN).replace("\n", " ")
        if not text:
            return None
        out["text"] = text
    elif ctype == "min_words":
        try:
            count = int(item.get("count") or item.get("value") or 0)
        except (TypeError, ValueError):
            return None
        if not 10 <= count <= 20000:
            return None
        out["count"] = count
    label = _clean_text(item.get("label") or "", MAX_LABEL)
    out["label"] = label or _default_check_label(out)
    return out


def normalize_checks(items) -> list:
    if not isinstance(items, list):
        return []
    result = []
    seen = set()
    for item in items:
        clean = normalize_check(item)
        if not clean:
            continue
        key = (clean["type"], clean.get("pattern", ""), clean.get("text", "").casefold(), clean.get("count", 0))
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= MAX_CHECKS:
            break
    return result


def normalize_inputs(items) -> list:
    if not isinstance(items, list):
        return []
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name") or "", 32).lower()
        name = re.sub(r"[^a-z0-9_-]", "", name)
        if not name or name in seen:
            continue
        seen.add(name)
        label = _clean_text(item.get("label") or name.replace("-", " ").replace("_", " ").title(), 60)
        placeholder = _clean_text(item.get("placeholder") or "", 100)
        result.append({
            "name": name,
            "label": label,
            "placeholder": placeholder,
            "required": bool(item.get("required", False)),
        })
        if len(result) >= MAX_INPUTS:
            break
    return result


def normalize_permissions(value, output: str = "") -> dict:
    raw = value if isinstance(value, dict) else {}
    implies_writes = output in WORKFLOW_OUTPUTS or output == "markdown"
    writes = bool(raw.get("writes", implies_writes))
    workspace = bool(raw.get("workspace", writes))
    web = bool(raw.get("web", False))
    return {"workspace": workspace, "writes": writes, "web": web}


def validate_skill(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Skill must be a JSON object.")
    name_raw = raw.get("name")
    if not isinstance(name_raw, str):
        raise ValueError("Skill is missing a slash name.")
    name = name_raw.strip().lstrip("/").lower()
    if not NAME_RE.fullmatch(name):
        raise ValueError("Skill name must start with a letter and use only lowercase letters, numbers, and hyphens (max 32).")
    if name in RESERVED_NAMES:
        raise ValueError(f"/{name} is a built-in Pipkin command or recipe and cannot be overwritten.")
    instructions = _clean_text(raw.get("instructions"), MAX_INSTRUCTIONS + 1)
    if not instructions:
        raise ValueError(f"/{name} is missing workflow instructions.")
    if len(instructions) > MAX_INSTRUCTIONS:
        raise ValueError(f"/{name} instructions exceed the {MAX_INSTRUCTIONS}-character limit.")
    description = _clean_text(raw.get("description") or "", MAX_DESCRIPTION)
    author = _clean_text(raw.get("author") or "", MAX_AUTHOR)
    icon = _clean_text(raw.get("icon") or "", 24).lower()
    if icon not in ALLOWED_ICONS:
        icon = "sparkle"
    output = _clean_text(raw.get("output") or "", 24).lower()
    if output not in ALLOWED_OUTPUTS:
        output = ""
    inputs = normalize_inputs(raw.get("inputs"))
    checks = normalize_checks(raw.get("checks"))
    permissions = normalize_permissions(raw.get("permissions"), output=output)

    skill = {
        "name": name,
        "description": description,
        "instructions": instructions,
    }
    if author:
        skill["author"] = author
    if icon != "sparkle":
        skill["icon"] = icon
    if output:
        skill["output"] = output
    if inputs:
        skill["inputs"] = inputs
    if checks:
        skill["checks"] = checks
    if any(permissions.values()) or isinstance(raw.get("permissions"), dict):
        skill["permissions"] = permissions
    return skill


def _parse_check_shorthand(token: str) -> Optional[dict]:
    text = token.strip()
    if not text:
        return None
    match = re.fullmatch(r"([a-z_]+)(?:\((.*)\))?", text, re.IGNORECASE)
    if not match:
        return None
    ctype = match.group(1).lower()
    arg = (match.group(2) or "").strip().strip("\"'")
    if ctype == "file_exists":
        return {"type": "file_exists", "pattern": arg or "*"}
    if ctype in ("html_links", "citations", "no_placeholders"):
        return {"type": ctype}
    if ctype in ("contains_heading", "contains_text"):
        return {"type": ctype, "text": arg} if arg else None
    if ctype == "min_words":
        return {"type": "min_words", "count": arg} if arg else None
    return None


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _slug_from_filename(filename: str) -> str:
    stem = Path(filename).name
    for suffix in (".pipkin-skill.md", ".pipkin-skill", ".json", ".md"):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    slug = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:MAX_NAME]
    return slug


def parse_markdown_skill(text: str, filename: str = "") -> dict:
    cleaned = text.replace("\r\n", "\n").lstrip("\ufeff")
    meta = {}
    body = cleaned
    if cleaned.startswith("---\n"):
        end = cleaned.find("\n---", 4)
        if end != -1:
            frontmatter = cleaned[4:end]
            body = cleaned[end + 4:].lstrip("\n")
            current_list_key = None
            for line in frontmatter.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("- ") and current_list_key:
                    meta.setdefault(current_list_key, []).append(stripped[2:].strip())
                    continue
                current_list_key = None
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip().lower()
                    val = val.strip()
                    if not val:
                        current_list_key = key
                        meta.setdefault(key, [])
                    else:
                        meta[key] = val.strip("\"'")

    checks_raw = meta.get("checks")
    checks = []
    if isinstance(checks_raw, list):
        checks = checks_raw
    elif isinstance(checks_raw, str):
        checks = [part.strip() for part in re.split(r",(?![^()]*\))", checks_raw) if part.strip()]

    permissions = {}
    for pkey in ("workspace", "writes", "web"):
        if pkey in meta and isinstance(meta[pkey], str):
            permissions[pkey] = _parse_bool(meta[pkey])

    payload = {
        "name": meta.get("name") or _slug_from_filename(filename),
        "description": meta.get("description") or "",
        "author": meta.get("author") or "",
        "icon": meta.get("icon") or "sparkle",
        "output": meta.get("output") or "",
        "instructions": body.strip(),
        "checks": checks,
    }
    if permissions:
        payload["permissions"] = permissions
    return validate_skill(payload)


def parse_skill_payload(raw_text: str, filename: str = "") -> dict:
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Skill file is empty.")
    stripped = raw_text.lstrip("\ufeff").strip()
    skills_out = []
    warnings = []
    is_pack = False

    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise ValueError(f"Could not parse JSON skill file: {error.msg}.") from None
        if isinstance(data, dict) and (data.get("format") == SKILL_PACK_FORMAT or isinstance(data.get("skills"), list)):
            is_pack = True
            entries = data.get("skills")
            if not isinstance(entries, list) or not entries:
                raise ValueError("Skill pack contains no skills.")
            for index, entry in enumerate(entries[:MAX_SKILLS_IN_PACK], 1):
                try:
                    skills_out.append(validate_skill(entry))
                except ValueError as error:
                    warnings.append(f"Skipped skill #{index}: {error}")
        elif isinstance(data, list):
            is_pack = True
            for index, entry in enumerate(data[:MAX_SKILLS_IN_PACK], 1):
                try:
                    skills_out.append(validate_skill(entry))
                except ValueError as error:
                    warnings.append(f"Skipped skill #{index}: {error}")
        elif isinstance(data, dict):
            fmt = data.get("format")
            if fmt is not None and fmt != SKILL_FORMAT:
                raise ValueError("Unsupported file format (expected a .pipkin-skill file).")
            skills_out.append(validate_skill(data))
        else:
            raise ValueError("Unsupported skill file structure.")
    else:
        skills_out.append(parse_markdown_skill(raw_text, filename=filename))

    if not skills_out:
        raise ValueError(warnings[0] if warnings else "No valid skills found in this file.")
    return {"skills": skills_out, "warnings": warnings, "is_pack": is_pack}


def serialize_skill(skill: dict) -> str:
    clean = validate_skill(skill)
    doc = {
        "format": SKILL_FORMAT,
        "version": SKILL_FORMAT_VERSION,
        **clean,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def inspect_skill_security(skill: dict) -> dict:
    """Static safety review of a skill's instructions and fields before install or run."""
    parts = [
        skill.get("description") or "",
        skill.get("instructions") or "",
    ]
    for item in skill.get("inputs") or []:
        parts.append(f"{item.get('label', '')} {item.get('placeholder', '')}")
    for check in skill.get("checks") or []:
        parts.append(f"{check.get('pattern', '')} {check.get('text', '')} {check.get('label', '')}")
    haystack = "\n".join(parts)

    flags = []
    for kind, label, pattern in SECURITY_RULES:
        match = pattern.search(haystack)
        if match:
            start = max(0, match.start() - 28)
            end = min(len(haystack), match.end() + 36)
            excerpt = haystack[start:end].replace("\n", " ").strip()
            flags.append({"kind": kind, "label": label, "excerpt": excerpt})

    if flags:
        summary = f"Review {len(flags)} flagged {'pattern' if len(flags) == 1 else 'patterns'} before installing or running."
        level = "review"
    else:
        summary = "Text instructions and local checks only. Cannot run code or change your approval settings."
        level = "standard"
    return {"level": level, "flags": flags, "summary": summary}


def _skill_filename_ok(name: str) -> bool:
    if not name or name.startswith(".") or "/" in name or "\\" in name or "\x00" in name:
        return False
    lower = name.casefold()
    return lower.endswith((".pipkin-skill", ".json", ".md"))


def load_workspace_skills(workspace: "agent.Workspace", check: Optional[Callable[[], None]] = None) -> dict:
    """Safely load project skills from <workspace>/.pipkin/skills/ without following symlinks."""
    import agent
    if workspace is None:
        return {"skills": [], "warnings": []}
    try:
        with workspace._directory([".pipkin", "skills"], create=False) as folder:
            entries = []
            with os.scandir(folder) as listing:
                for entry in listing:
                    if not _skill_filename_ok(entry.name) or entry.is_symlink():
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if agent._is_reparse_point(st) or not stat.S_ISREG(st.st_mode):
                        continue
                    # Windows DirEntry.stat() does not report link counts. The
                    # Windows open path checks the real metadata before reading.
                    if not workspace._windows and not agent.ordinary_link(st):
                        continue
                    entries.append((entry.name, st.st_size))
            entries.sort(key=lambda pair: pair[0].casefold())

            loaded = []
            seen_names = set()
            warnings = []
            for filename, size in entries:
                if check:
                    check()
                if len(loaded) >= MAX_WORKSPACE_SKILLS:
                    warnings.append(f"Reached the {MAX_WORKSPACE_SKILLS}-skill workspace limit.")
                    break
                if size == 0 or size > MAX_SKILL_FILE_BYTES:
                    warnings.append(f"Skipped .pipkin/skills/{filename} (empty or over 32 KB).")
                    continue
                try:
                    if workspace._windows:
                        target = folder / filename
                        info = workspace._win_check_entry(target)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            continue
                        with target.open("rb") as stream:
                            raw = stream.read(MAX_SKILL_FILE_BYTES + 1)
                    else:
                        fd = os.open(filename, agent.FILE_FLAGS, dir_fd=folder)
                        try:
                            info = os.fstat(fd)
                            if not stat.S_ISREG(info.st_mode) or not agent.ordinary_link(info):
                                continue
                            with os.fdopen(os.dup(fd), "rb") as stream:
                                raw = stream.read(MAX_SKILL_FILE_BYTES + 1)
                        finally:
                            os.close(fd)
                    if len(raw) > MAX_SKILL_FILE_BYTES or b"\x00" in raw:
                        warnings.append(f"Skipped .pipkin/skills/{filename} (binary or over 32 KB).")
                        continue
                    text = raw.decode("utf-8")
                    parsed = parse_skill_payload(text, filename=filename)
                    warnings.extend(f".pipkin/skills/{filename}: {w}" for w in parsed["warnings"])
                    for item in parsed["skills"]:
                        if item["name"] in seen_names:
                            warnings.append(f"Duplicate /{item['name']} in .pipkin/skills/{filename} ignored.")
                            continue
                        seen_names.add(item["name"])
                        item["source"] = "workspace"
                        item["file"] = f".pipkin/skills/{filename}"
                        item["security"] = inspect_skill_security(item)
                        loaded.append(item)
                        if len(loaded) >= MAX_WORKSPACE_SKILLS:
                            break
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    warnings.append(f"Skipped .pipkin/skills/{filename}: {error}")
            return {"skills": loaded, "warnings": warnings[:12]}
    except (FileNotFoundError, NotADirectoryError):
        return {"skills": [], "warnings": []}
    except (OSError, ValueError) as error:
        return {"skills": [], "warnings": [f"Could not read .pipkin/skills: {error}"]}


def save_workspace_skill(workspace: "agent.Workspace", raw_skill: dict) -> dict:
    """Write a validated skill into <workspace>/.pipkin/skills/<name>.pipkin-skill safely."""
    import agent
    if workspace is None:
        raise ValueError("Choose a workspace folder first.")
    skill = validate_skill(raw_skill)
    serialized = serialize_skill(skill).encode("utf-8")
    if len(serialized) > MAX_SKILL_FILE_BYTES:
        raise ValueError("Skill file exceeds the 32 KB limit.")
    filename = f"{skill['name']}.pipkin-skill"
    with workspace._directory([".pipkin", "skills"], create=True) as folder:
        target_dir = folder if workspace._windows else (workspace.path / ".pipkin" / "skills")
        target = target_dir / filename
        if target.exists() or target.is_symlink():
            info = os.lstat(target)
            if stat.S_ISLNK(info.st_mode) or agent._is_reparse_point(info) or not stat.S_ISREG(info.st_mode):
                raise ValueError(f".pipkin/skills/{filename} is not a regular file.")
            if not agent.ordinary_link(info):
                raise ValueError(f".pipkin/skills/{filename} is hard-linked to another user's file.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=target_dir, prefix=".pipkin-skill-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            if sys.platform != "win32":
                os.chmod(temporary, 0o644)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    skill["source"] = "workspace"
    skill["file"] = f".pipkin/skills/{filename}"
    skill["security"] = inspect_skill_security(skill)
    return {"path": skill["file"], "skill": skill}


def _count_words(text: str) -> int:
    return sum(1 for token in WORD_TOKEN_RE.findall(text or "") if WORD_ALNUM_RE.search(token))


def _extract_headings(text: str, path: str = "") -> list:
    headings = []
    if path.casefold().endswith((".html", ".htm")) or "<h1" in text.lower() or "<h2" in text.lower():
        for match in HTML_HEADING_RE.findall(text):
            plain = HTML_TAG_RE.sub(" ", match)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                headings.append(plain)
    for match in MD_HEADING_RE.findall(text):
        plain = re.sub(r"\s+", " ", match).strip()
        if plain:
            headings.append(plain)
    return headings


def _plain_text(text: str, path: str = "") -> str:
    if path.casefold().endswith((".html", ".htm")):
        without_scripts = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        return re.sub(r"\s+", " ", HTML_TAG_RE.sub(" ", without_scripts)).strip()
    return text


def run_skill_checks(
    checks: list,
    workspace: Optional["agent.Workspace"],
    saved_files: list,
    result_text: str = "",
    check_cancel: Optional[Callable[[], None]] = None,
) -> list:
    """Deterministically verify a skill's declarative checks against saved files (or final reply)."""
    import agent
    normalized = normalize_checks(checks)
    if not normalized:
        return []

    unique_files = list(dict.fromkeys(saved_files or []))
    file_texts = {}
    if workspace is not None:
        for rel in unique_files:
            if check_cancel:
                check_cancel()
            try:
                text, _ = workspace.raw_text(rel)
                file_texts[rel] = text
            except (OSError, ValueError):
                continue

    # Prefer saved file contents when the run wrote files; fall back to the final answer otherwise.
    corpus_entries = [(rel, text) for rel, text in file_texts.items()]
    if not corpus_entries and result_text:
        corpus_entries = [("", result_text)]

    results = []
    for spec in normalized:
        if check_cancel:
            check_cancel()
        ctype = spec["type"]
        label = spec["label"]

        if ctype == "file_exists":
            pattern = spec.get("pattern") or "*"
            matched = [
                rel for rel in unique_files
                if fnmatch.fnmatch(rel.casefold(), pattern.casefold())
                or fnmatch.fnmatch(Path(rel).name.casefold(), pattern.casefold())
            ]
            if matched:
                results.append({
                    "type": ctype, "label": label, "ok": True,
                    "detail": "Saved " + ", ".join(matched[:3]),
                    "path": matched[0],
                })
            else:
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": f"No file matching {pattern} was saved in this run.",
                })

        elif ctype == "html_links":
            html_files = [rel for rel in unique_files if rel.casefold().endswith((".html", ".htm"))]
            if not html_files:
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": "No HTML file was saved to check.",
                })
            elif workspace is None:
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": "No workspace selected to verify local HTML links.",
                })
            else:
                issues = []
                for rel in html_files:
                    try:
                        report = workspace.check_page(rel, check_cancel or (lambda: None))
                        if not report.endswith(agent.LINKS_OK):
                            issues.append(f"{rel}: {report.splitlines()[-1]}")
                    except (OSError, ValueError) as error:
                        issues.append(f"{rel}: {error}")
                if issues:
                    results.append({
                        "type": ctype, "label": label, "ok": False,
                        "detail": "; ".join(issues[:2]),
                        "path": html_files[0],
                    })
                else:
                    results.append({
                        "type": ctype, "label": label, "ok": True,
                        "detail": f"Checked {len(html_files)} HTML {'file' if len(html_files) == 1 else 'files'}: all local links valid.",
                        "path": html_files[0],
                    })

        elif ctype == "contains_heading":
            needle = spec["text"].casefold()
            found_path = None
            found_heading = None
            for rel, text in corpus_entries:
                for heading in _extract_headings(text, rel):
                    if needle in heading.casefold():
                        found_path = rel
                        found_heading = heading
                        break
                if found_heading:
                    break
            if found_heading:
                where = f" in {found_path}" if found_path else ""
                results.append({
                    "type": ctype, "label": label, "ok": True,
                    "detail": f"Found “{found_heading[:60]}”{where}.",
                    **({"path": found_path} if found_path else {}),
                })
            else:
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": f"No heading matching “{spec['text']}” was found.",
                })

        elif ctype == "contains_text":
            needle = spec["text"].casefold()
            found_path = None
            matched = False
            for rel, text in corpus_entries:
                if needle in text.casefold() or needle in _plain_text(text, rel).casefold():
                    matched = True
                    found_path = rel
                    break
            if matched:
                where = f" in {found_path}" if found_path else ""
                results.append({
                    "type": ctype, "label": label, "ok": True,
                    "detail": f"Found “{spec['text']}”{where}.",
                    **({"path": found_path} if found_path else {}),
                })
            else:
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": f"Missing required text “{spec['text']}”.",
                })

        elif ctype == "min_words":
            minimum = spec["count"]
            total_words = sum(_count_words(_plain_text(text, rel)) for rel, text in corpus_entries)
            primary = next((rel for rel, _ in corpus_entries if rel), "")
            if total_words >= minimum:
                results.append({
                    "type": ctype, "label": label, "ok": True,
                    "detail": f"{total_words:,} words (minimum {minimum:,}).",
                    **({"path": primary} if primary else {}),
                })
            else:
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": f"Only {total_words:,} words found (minimum {minimum:,}).",
                    **({"path": primary} if primary else {}),
                })

        elif ctype == "citations":
            found_path = None
            matched_sample = None
            for rel, text in corpus_entries:
                match = CITATION_RE.search(text)
                if match:
                    found_path = rel
                    matched_sample = match.group(0)[:60]
                    break
            if matched_sample:
                where = f" in {found_path}" if found_path else ""
                results.append({
                    "type": ctype, "label": label, "ok": True,
                    "detail": f"Found citation/link ({matched_sample}){where}.",
                    **({"path": found_path} if found_path else {}),
                })
            else:
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": "No source citations, page references, or links were found.",
                })

        elif ctype == "no_placeholders":
            hit_path = None
            hit_text = None
            for rel, text in corpus_entries:
                match = PLACEHOLDER_RE.search(_plain_text(text, rel))
                if match:
                    hit_path = rel
                    hit_text = match.group(0)
                    break
            if hit_text:
                where = f" in {hit_path}" if hit_path else ""
                results.append({
                    "type": ctype, "label": label, "ok": False,
                    "detail": f"Found unfinished placeholder “{hit_text}”{where}.",
                    **({"path": hit_path} if hit_path else {}),
                })
            else:
                primary = next((rel for rel, _ in corpus_entries if rel), "")
                results.append({
                    "type": ctype, "label": label, "ok": True,
                    "detail": "No TODO, TBD, or placeholder text found.",
                    **({"path": primary} if primary else {}),
                })

    return results


def format_checks_for_prompt(checks: list) -> str:
    normalized = normalize_checks(checks)
    if not normalized:
        return ""
    lines = ["Pipkin will automatically verify these skill checks when you finish:"]
    for spec in normalized:
        lines.append("- " + spec["label"])
    lines.append("Make sure your saved output satisfies every check above before giving your final answer.")
    return "\n".join(lines)


def format_failed_checks_nudge(results: list) -> str:
    failed = [item for item in results if not item.get("ok")]
    if not failed:
        return ""
    lines = ["Pipkin ran this skill's verification checks and found items that still need fixing:"]
    for item in failed:
        lines.append(f"- {item['label']}: {item['detail']}")
    lines.append("Fix these issues now (update the saved file or complete the missing requirement), then give your final summary.")
    return "\n".join(lines)
