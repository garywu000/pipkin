"""Scheduled background tasks: when they run, where their results go, and the loop that runs them.

Tasks run only while Pipkin is open. A run that came due while Pipkin was closed or the computer was asleep is not
started on its own: the task is marked missed so the user can run or skip it. Every run is unattended, so the server
starts it read-only or new-files-only, without shell commands, and with hard step and time limits. A run waits while
the model is busy or memory pressure is critical, and is skipped if that lasts too long.
"""
import datetime
from contextlib import contextmanager
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path

import skills as skills_module

KINDS = ("once", "daily", "weekdays", "weekly")
ACCESS = ("read", "create")
MISSED_GRACE = datetime.timedelta(minutes=10)    # a run this late counts as missed instead of starting by itself
DEFER_RETRY = datetime.timedelta(minutes=1)
DEFER_LIMIT = datetime.timedelta(minutes=45)     # stop waiting for a busy model or memory and record a skip
TICK_SECONDS = 30
TASK_LIMIT = 30
INBOX_LIMIT = 60
TITLE_LIMIT = 80
PROMPT_LIMIT = 4000

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
FULL_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# ---------------------------------------------------------------------------------------------------------------
# Reading a schedule the user asked for in their own words
#
# The model proposes a task with the schedule_task tool. Small models often get the details wrong ("6pm" as 06:00,
# "Monday and Thursday" as Monday only, "next Friday" as today), so whatever the user literally typed overrides the
# model's arguments. Anything Pipkin had to guess is listed in needsClarification for the confirmation card.
# ---------------------------------------------------------------------------------------------------------------
_DAY_INDEX = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3,
              "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_DAY_ALT = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues|tue|wed|thurs|thur|thu|fri|sat|sun)"
_DAY_WORD = re.compile(rf"\b(?P<day>{_DAY_ALT})(?P<plural>s)?\b", re.I)
# Short forms ("sun", "sat", "wed") are ordinary words too, so they count only next to list or schedule words.
_ABBR_CONTEXT = re.compile(r"(?:\b(?:every|each|on|next|this|and|or|to|through|thru)\s+|[,/&-]\s*)$", re.I)
_RECURRING_DAY = re.compile(r"\b(?:every|each)\s+(?:single\s+)?$", re.I)
_ONE_DAY = re.compile(r"\b(?:next|this|coming|on)\s+$", re.I)
_WEEKDAYS = re.compile(r"\b(?:every|each|on)\s+weekdays?\b|\bweekdays\b|\b(?:every|each)\s+work\s*day\b", re.I)
_WEEKENDS = re.compile(r"\b(?:every|each|on)\s+weekends?\b|\bweekends\b", re.I)
_THIS_WEEKEND = re.compile(r"\b(?:this|the|next)\s+weekend\b", re.I)
_DAILY = re.compile(r"\b(?:every|each)\s+(?:single\s+)?(?:day|morning|afternoon|evening|night)\b|\bnightly\b", re.I)
# "daily"/"weekly" describe a recurring deliverable ("a daily summary", "check my notes daily"), but not an existing
# thing ("the daily log", "my weekly report").
_CADENCE_ADJ = re.compile(r"\b(?P<word>daily|weekly)\b", re.I)
_OWNED_BEFORE = re.compile(r"\b(?:the|my|your|our|his|her|their|its|this|that|today'?s|yesterday'?s)\s+$", re.I)
_EVERY_WEEK = re.compile(r"\b(?:every|each|once\s+a)\s+week\b", re.I)
_UNSUPPORTED = (
    (re.compile(r"\bevery\s+(?:other|second|2nd|third|3rd)\b|\b(?:bi-?weekly|fortnightly)\b|\bevery\s+\d+\s+(?:days|weeks)\b", re.I),
     "every other day or week"),
    (re.compile(r"\b(?:every|each)\s+(?:\d+\s+)?(?:hours?|minutes?|mins?)\b|\b(?:hourly|half-hourly)\b", re.I), "every hour or minute"),
    (re.compile(r"\b(?:every|each|once\s+a)\s+(?:month|year)\b|\b(?:monthly|yearly|annually)\b|"
                r"\b(?:first|last|1st|15th)\s+(?:\w+\s+)?of\s+(?:the|every|each)\s+month\b|\bend\s+of\s+(?:the|every|each)\s+month\b", re.I),
     "monthly or yearly"),
    (re.compile(r"\b(?:twice|three\s+times|\d+\s+times)\s+(?:a|per|each)\s+(?:day|week)\b", re.I), "several times a day or week"),
)
_TIME = re.compile(
    r"(?<![\d:.-])(?:(?P<h>\d{1,2})(?::(?P<m>[0-5]\d))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z])"
    r"|(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)(?![\d:])"
    r"|\b(?P<noon>noon|midday)\b|\b(?P<midnight>midnight)\b"
    r"|\b(?:at|around|by)\s+(?P<bare>\d{1,2})(?![\d:]|\s*(?:%|am|pm|a\.m|p\.m|th|st|nd|rd|days?|weeks?|hours?|minutes?|mins?|files?|items?|pages?)))",
    re.I)
_PM_WORDS = re.compile(r"\b(?:afternoon|evening|night|tonight|after\s+school|after\s+work|dinner)\b", re.I)
_AM_WORDS = re.compile(r"\b(?:morning|breakfast|before\s+school|before\s+work|sunrise)\b", re.I)
_PART_OF_DAY = (("morning", "08:00"), ("afternoon", "14:00"), ("evening", "18:00"), ("tonight", "20:00"), ("night", "20:00"))
_TOMORROW = re.compile(r"\btomorrow\b(?!['’]s)", re.I)
_DAY_AFTER_TOMORROW = re.compile(r"\bday\s+after\s+tomorrow\b", re.I)
_TODAY = re.compile(r"\b(?:today|tonight|this\s+(?:morning|afternoon|evening))\b(?!['’]s)", re.I)
_ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
           "oct": 10, "nov": 11, "dec": 12}
_MONTH = r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MONTH_DAY = re.compile(rf"\b{_MONTH}\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d\d))?\b|"
                        rf"\b(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<month2>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
                        r"apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", re.I)
_IN_OFFSET = re.compile(r"\bin\s+(?:(?P<one>an?|one)\s+|(?P<num>\d{1,3})\s+)(?P<unit>minutes?|mins?|hours?|hrs?|days?)\b", re.I)
_EXPLICIT_ONCE = re.compile(r"\b(?:once|one[- ]time|just\s+once|tomorrow|today|tonight|next\s+weekend|this\s+weekend)\b|"
                            rf"\bnext\s+{_DAY_ALT}\b|\b20\d\d-\d\d-\d\d\b", re.I)
_IMPERATIVE_ACTION = re.compile(
    r"\b(?:please|remind\s+me|schedule|summarize|condense|check|read|write|save|create|send|run|generate|"
    r"prepare|review|update|draft|build|compile|collect|make|turn|convert|email|notify|alert|scan|monitor|track)\b",
    re.I,
)


def _day_mentions(text):
    """(weekday, recurring, one_time) for each day name the user wrote."""
    found = []
    for match in _DAY_WORD.finditer(text):
        if re.match(r"['’]s\b", text[match.end():], re.I):
            continue
        word = match.group("day").lower()
        before = text[max(0, match.start() - 24):match.start()]
        if not word.endswith("day"):
            after = text[match.end():match.end() + 6]
            if not (_ABBR_CONTEXT.search(before) or re.match(r"\s*(?:[,/&-]|and\b|or\b)", after, re.I)):
                continue
        found.append((_DAY_INDEX[word], bool(match.group("plural")) or bool(_RECURRING_DAY.search(before)),
                      bool(_ONE_DAY.search(before))))
    return found


def _clock_times(text):
    """Every time of day the user wrote: (hour, minute, exact). exact=False means an hour without am/pm."""
    times = []
    for match in _TIME.finditer(text):
        if match.group("noon"):
            times.append((12, 0, True))
        elif match.group("midnight"):
            times.append((0, 0, True))
        elif match.group("h") is not None:
            hour, minute = int(match.group("h")), int(match.group("m") or 0)
            if not 1 <= hour <= 12:
                continue
            pm = match.group("ap").lower().startswith("p")
            times.append(((hour % 12) + (12 if pm else 0), minute, True))
        elif match.group("h24") is not None:
            raw_h = match.group("h24")
            hour, minute = int(raw_h), int(match.group("m24"))
            # Leading zero ("08:00", "06:30") or hour > 12 is unambiguous 24-hour notation.
            times.append((hour, minute, hour == 0 or hour > 12 or (len(raw_h) == 2 and raw_h.startswith("0"))))
        else:
            hour = int(match.group("bare"))
            if 1 <= hour <= 23:
                times.append((hour, 0, hour > 12))
    unique = []
    for item in times:
        if item[:2] not in [seen[:2] for seen in unique]:
            unique.append(item)
    return unique


def _resolve_hour(hour, text, model_hour=None):
    """Pick am or pm for an hour written without it. Returns (hour, sure)."""
    if _PM_WORDS.search(text):
        return (hour % 12) + 12 if hour != 12 else 12, True
    if _AM_WORDS.search(text):
        return hour % 12, True
    if model_hour is not None and model_hour % 12 == hour % 12:
        return model_hour, False
    # Without a clue, follow ordinary habits: 7-11 is morning, 12 is noon, 1-6 is afternoon or evening.
    return (hour if 7 <= hour <= 12 else (hour % 12) + 12), False


def _next_weekday(today, weekday, *, skip_today=False):
    ahead = (weekday - today.weekday()) % 7
    if ahead == 0 and skip_today:
        ahead = 7
    return today + datetime.timedelta(days=ahead)


# An explicitly described event is reference material, even when its name includes a date or clock time.
_EVENT_REFERENCE = re.compile(
    rf"\b(?:(?:the|my|your|our|their|his|her)\s+(?:(?:{_DAY_ALT}|today|tomorrow)(?:['’]s)?\s+)?|"
    rf"(?:{_DAY_ALT}|today|tomorrow)['’]s\s+)"
    r"(?:\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)|\d{1,2}:\d{2}|morning|afternoon|evening)\s+"
    r"(?:meeting|appointment|class|lecture|event|flight|train|session|deadline|webinar|call)\b", re.I)
_REFERENCE_RANGE = re.compile(r"\b(?:from|between|since|during|about|for)\s+$", re.I)


def _timing_text(text):
    return _EVENT_REFERENCE.sub(" ", text or "")


def read_timing(text, now):
    """What the user's own words say about when a task runs. Every key is None when the text doesn't say."""
    text = _timing_text(text)
    result = {"kind": None, "days": None, "date": None, "time": None, "unsupported": None, "problem": None, "flags": set(),
              "ambiguous_hour": None}
    for pattern, label in _UNSUPPORTED:
        if pattern.search(text):
            result["unsupported"] = label
            return result
    today = now.date()
    days = _day_mentions(text)
    recurring_days = any(recurring for _, recurring, _ in days)
    # A recurring day list is a contiguous timing expression, not every day name in the instructions.
    day_range = re.search(rf"\b({_DAY_ALT})s?\s*(?:-|–|to|through|thru)\s*({_DAY_ALT})s?\b", text, re.I)
    day_list = re.search(rf"\b(?:every|each)\s+(?:single\s+)?({_DAY_ALT}s?(?:\s*(?:,\s*(?:and\s+)?|and\s+|&\s+|/\s*){_DAY_ALT}s?)*)", text, re.I)
    recurring_list = _day_mentions("every " + day_list.group(1)) if day_list else []
    if day_range and (_REFERENCE_RANGE.search(text[:day_range.start()])
                      or (day_list and day_range.start() != day_list.start(1))
                      or (_DAILY.search(text) and not day_list)):
        day_range = None
    if day_range:
        first, last = (_DAY_INDEX[part.lower()] for part in day_range.groups())
        range_days = sorted((first + offset) % 7 for offset in range((last - first) % 7 + 1))
    else:
        range_days = []
    cadence_words = [match for match in _CADENCE_ADJ.finditer(text) if not _OWNED_BEFORE.search(text[:match.start()])]
    if range_days == list(range(5)) or _WEEKDAYS.search(text):
        result["kind"] = "weekdays"
    elif range_days:
        result["kind"], result["days"] = "weekly", range_days
    elif _WEEKENDS.search(text) and not _THIS_WEEKEND.search(text):
        result["kind"], result["days"] = "weekly", [5, 6]
    elif recurring_days or (days and (_EVERY_WEEK.search(text) or any(m.group("word").lower() == "weekly" for m in cadence_words))):
        result["kind"] = "weekly"
        result["days"] = sorted({day for day, _, _ in recurring_list} or {day for day, _, one_time in days if not one_time} or {day for day, _, _ in days})
    elif _DAILY.search(text) or any(m.group("word").lower() == "daily" for m in cadence_words):
        result["kind"] = "daily"
    elif _EVERY_WEEK.search(text) or any(m.group("word").lower() == "weekly" for m in cadence_words):
        result["kind"] = "weekly"   # the day is left for the model, and flagged
    elif len({day for day, _, _ in days}) >= 2 and not _REFERENCE_RANGE.search(text):
        # Multiple distinct weekdays ("on Monday and Thursday at 5pm"): treat as weekly and flag kind for review.
        result["kind"] = "weekly"
        result["days"] = sorted({day for day, _, _ in days})
        result["flags"].add("kind")
    else:
        date = None
        offset_time = None
        iso_match = _ISO_DATE.search(text)
        month_match = _MONTH_DAY.search(text)
        in_match = _IN_OFFSET.search(text)
        if _DAY_AFTER_TOMORROW.search(text):
            date = today + datetime.timedelta(days=2)
        elif _TOMORROW.search(text):
            date = today + datetime.timedelta(days=1)
        elif in_match:
            amount = 1 if in_match.group("one") else int(in_match.group("num"))
            unit = in_match.group("unit").lower()
            if unit.startswith("min") and 1 <= amount <= 999:
                target = (now + datetime.timedelta(minutes=amount)).replace(second=0, microsecond=0)
                date, offset_time = target.date(), target.strftime("%H:%M")
            elif unit.startswith(("hour", "hr")) and 1 <= amount <= 24 * 365:
                target = (now + datetime.timedelta(hours=amount)).replace(second=0, microsecond=0)
                date, offset_time = target.date(), target.strftime("%H:%M")
            elif unit.startswith("day") and 1 <= amount <= 365:
                date = today + datetime.timedelta(days=amount)
            else:
                result["problem"] = "Choose a positive delay within the supported scheduling range."
                return result
        elif iso_match:
            try:
                date = datetime.date(*(int(part) for part in iso_match.groups()))
            except ValueError:
                result["problem"] = "That calendar date is invalid."
                return result
        elif month_match:
            month = _MONTHS[(month_match.group("month") or month_match.group("month2")).lower()[:3]]
            day = int(month_match.group("day") or month_match.group("day2"))
            year = int(month_match.group("year")) if month_match.group("year") else today.year
            try:
                date = datetime.date(year, month, day)
                if date < today and not month_match.group("year"):
                    date = datetime.date(year + 1, month, day)
            except ValueError:
                result["problem"] = "That calendar date is invalid."
                return result
        elif _THIS_WEEKEND.search(text):
            date = _next_weekday(today, 5)
            if re.search(r"\bnext\s+weekend\b", text, re.I):
                date += datetime.timedelta(days=7)
            result["flags"].add("date")
        elif days:
            weekday = days[0][0]
            is_next = bool(re.search(rf"\bnext\s+{_DAY_ALT}\b", text, re.I))
            date = _next_weekday(today, weekday, skip_today=is_next)
            if is_next:
                result["flags"].add("date")   # "next Friday" can mean this Friday or the one after
        elif _TODAY.search(text):
            date = today
        if date is not None:
            result["kind"], result["date"] = "once", date.isoformat()
            if offset_time is not None:
                result["time"] = offset_time
    for clock in re.finditer(r"\b(?:at|around|by)\s+(\d{1,2})(?::(\d{2}))?(?:\s*([ap]\.?m\.?))?(?![\d:]|[a-z])", text, re.I):
        hour, minute, meridiem = int(clock.group(1)), int(clock.group(2) or 0), clock.group(3)
        if minute > 59 or not (1 <= hour <= 12 if meridiem else 0 <= hour <= 23):
            result["problem"] = "That clock time is invalid."
            return result
    times = _clock_times(text)
    if len(times) > 1:
        if result["kind"] in ("daily", "weekdays", "weekly"):
            result["unsupported"] = "several times a day"
            return result
        result["flags"].add("time")
    if times:
        if result["kind"] == "once" and result["time"] and _IN_OFFSET.search(text):
            result["problem"] = "Choose either a relative delay or a specific clock time."
            return result
        hour, minute, exact = times[0]
        if exact or hour > 12:
            result["time"] = f"{hour:02d}:{minute:02d}"
        else:
            result["ambiguous_hour"] = (hour, minute)
    elif not result["time"]:
        lowered = text.lower()
        for word, default in _PART_OF_DAY:
            if re.search(rf"\b{word}\b", lowered):
                result["time"] = default
                result["flags"].add("time")
                break
    return result


def _model_time(value):
    """Accept the time formats small models produce: "18:00", "6:00", "18:00:00", "6pm", "6:30 PM"."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d:00", value):
        value = value.rsplit(":", 1)[0]
    if not re.fullmatch(r"(?:\d{1,2}(?::[0-5]\d)?\s*(?:[ap]\.?m\.?)?|noon|midday|midnight)", value, re.I):
        return None
    times = _clock_times(value) or _clock_times("at " + value)
    if len(times) != 1:
        return None
    return times[0][0], times[0][1]


def _model_days(value):
    if not isinstance(value, list):
        return []
    days = set()
    for item in value:
        if isinstance(item, str) and item.strip().lower() in _DAY_INDEX:
            days.add(_DAY_INDEX[item.strip().lower()])
    return sorted(days)


def _model_date(value, today):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + datetime.timedelta(days=1)
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _strip_timing(text):
    """The user's request without its timing words, for when the model leaves the instructions empty."""
    stripped = re.sub(rf"\b(?:every|each|on|next|this|at|around|by)\s+(?:{_DAY_ALT}s?|weekdays?|weekends?|"
                      r"day|week|morning|afternoon|evening|night)\b", " ", text, flags=re.I)
    stripped = re.sub(rf"(?:,|\b(?:and|or|through|to)\b)\s*\b{_DAY_ALT}s?\b", " ", stripped, flags=re.I)
    stripped = _TIME.sub(" ", stripped)
    stripped = re.sub(r"\b(?:daily|weekly|nightly|tomorrow|tonight|today)\b", " ", stripped, flags=re.I)
    # Drop connecting words left stranded where a time or day was removed ("notes at" / "at , summarize").
    stripped = re.sub(r"\b(?:at|around|by|on|every|each|and)\b(?=\s*(?:[,.;:!?]|$))", " ", stripped, flags=re.I)
    stripped = re.sub(r"^\s*(?:at|on|every|each|and|or)\b", " ", stripped, flags=re.I)
    stripped = re.sub(r"\s+([,.!?])", r"\1", re.sub(r"\s{2,}", " ", stripped)).strip(" ,;:")
    return stripped[:1].upper() + stripped[1:] if stripped else ""


_QUESTION = re.compile(r"^\s*(?:why|what|when|where|who|whom|whose|which|how|is|are|am|do|does|did|was|were|has|have|had|"
                       r"should|shall|isn't|aren't|don't|doesn't)\b|^\s*(?:please\s+)?(?:explain|describe|define|"
                       r"tell\s+me\s+(?:why|what|how|when|about|if|whether))\b", re.I)


def is_question(text):
    """True for a question about something ("Why do we have lab every Wednesday?"), not a polite request
    ("Can you check my notes every Friday?")."""
    text = (text or "").strip()
    if not text:
        return False
    text = re.sub(r"^\s*(?:please\s+)?(?:can|could|would|will)\s+you\s+|^\s*please\s+", "", text, flags=re.I)
    return bool(_QUESTION.search(text))


def user_request(task, explicit=None):
    """Keep appended document context out of scheduling and permission inference.

    Older clients and checkpoints only carry the assembled prompt. Their document
    headers are JSON-quoted filenames followed by an optional excerpt marker.
    """
    if explicit is not None:
        return explicit
    return re.split(r'\n\nAttached document "(?:[^"\\\n]|\\.)*"(?: \(excerpt\))?\n', task, maxsplit=1)[0]


def schedule_hint(text, now):
    """A short phrase for the timing in a request that reads like "do this later or on a schedule", else None.
    Used to remind the model to propose a scheduled task instead of starting the work now."""
    if not isinstance(text, str) or not text.strip() or text.lstrip().startswith("/") or is_question(text):
        return None
    # Statements, quoted text, and dates describing an existing item are weak evidence of scheduling.
    # Allow context-prefixed requests like "I keep my notes here. Summarize them every Monday at 7pm."
    if re.match(r"^\s*(?:I|we|they|he|she|it)\s+(?!want\b|need\b|would\s+like\b|['’]d\s+like\b)", text, re.I):
        clauses = [part.strip() for part in re.split(r"[.!?;—–\n]+", text) if part.strip()]
        has_request_clause = any(
            not re.match(r"^(?:I|we|they|he|she|it)\s+(?!want\b|need\b|would\s+like\b|['’]d\s+like\b)", clause, re.I)
            and _IMPERATIVE_ACTION.search(clause)
            for clause in clauses[1:]
        )
        if not has_request_clause:
            return None
    if re.match(r"^\s*(?:translate|rewrite|proofread|quote)\b", text, re.I) and not re.match(r"^\s*schedule\b", text, re.I):
        return None
    if re.search(r"\b(?:from|between|since|until|during|about|created|written|held|sent|received)\s+(?:on\s+)?"
                 rf"(?:{_DAY_ALT}|today|tomorrow|\d{{4}}-\d{{2}}-\d{{2}})\b", text, re.I) and not re.search(
                     r"\b(?:every|each|schedule|remind|nightly|daily|weekly)\b", text, re.I):
        return None
    timing = read_timing(text, now)
    # A lookup for a particular day's material is commonly an immediate request.
    # Leave these ambiguous cases to the model unless the user supplies a clock time or recurrence.
    if timing["kind"] == "once" and not timing["time"] and not timing["ambiguous_hour"] and re.search(
            rf"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:show|list|get|find|display)\b[^.!?\n]*"
            rf"(?:\bfor\s+|\b(?:calendar|agenda|appointments?|meetings?|events?)\s+)"
            rf"(?:today|tomorrow|(?:next\s+|this\s+)?{_DAY_ALT})\b", text, re.I):
        return None
    if timing["unsupported"]:
        return timing["unsupported"]
    if timing["problem"]:
        return timing["problem"]
    kind = timing["kind"]
    if kind is None:
        # A leading or explicit clock instruction can mean a one-time task.
        if (timing["time"] or timing["ambiguous_hour"]) and re.search(r"\b(?:schedule|remind|at|around)\b", text, re.I):
            return "at the requested time"
        return None
    if kind == "weekly":
        phrase = "every " + ", ".join(DAY_NAMES[day] for day in timing["days"]) if timing["days"] else "every week"
    elif kind == "weekdays":
        phrase = "on weekdays"
    elif kind == "daily":
        phrase = "every day"
    else:
        date = datetime.date.fromisoformat(timing["date"])
        phrase = ("today" if date == now.date() else "tomorrow" if date == now.date() + datetime.timedelta(days=1)
                  else date.strftime("%a %b ") + str(date.day))
    if timing["time"]:
        phrase += " at " + timing["time"]
    elif timing["ambiguous_hour"]:
        phrase += " at " + str(timing["ambiguous_hour"][0])
    return phrase


_CANCEL_SCHEDULE = re.compile(
    r"\b(?:cancel|skip|forget|drop|never\s*mind)\s+(?:the\s+|that\s+)?schedul\w*|"
    r"\b(?:don'?t|do\s+not)\s+schedule\b|"
    r"\b(?:just\s+)?(?:do|run)\s+(?:it|this)\s+(?:right\s+)?now(?:\s+instead)?\b",
    re.I,
)


def cancels_schedule(text):
    """True when a user explicitly cancels scheduling or asks to run now."""
    if not isinstance(text, str):
        return False
    # Treat direct instructions as cancellation, never questions or quoted examples.
    direct = re.sub(r"^\s*(?:(?:please|actually|okay|ok|instead)\s*[,.:]?\s*)+", "", text, flags=re.I)
    direct = re.sub(r"^(?:can|could|would|will)\s+you\s+", "", direct, flags=re.I)
    return bool(_CANCEL_SCHEDULE.match(direct.lstrip()))


def runs_now(text):
    """Cancellation alone never authorizes immediate execution."""
    return isinstance(text, str) and bool(re.search(
        r"\b(?:just\s+)?(?:do|run)\s+(?:it|this)\s+(?:right\s+)?now\b", text, re.I
    )) and not re.search(r"\b(?:don'?t|do\s+not|never)\s+(?:just\s+)?(?:do|run)\s+(?:it|this)\s+(?:right\s+)?now\b", text, re.I)


def describe_schedule(schedule):
    """Plain words for a schedule, e.g. "every Wed at 6:00 PM"."""
    hour, minute = parse_clock(schedule["time"])
    at = datetime.time(hour, minute).strftime("%I:%M %p").lstrip("0")
    if schedule["kind"] == "once":
        return datetime.date.fromisoformat(schedule["date"]).strftime("once on %a %b ") + \
            str(datetime.date.fromisoformat(schedule["date"]).day) + f" at {at}"
    if schedule["kind"] == "daily":
        return f"every day at {at}"
    if schedule["kind"] == "weekdays":
        return f"on weekdays at {at}"
    return "every " + ", ".join(DAY_NAMES[day] for day in schedule.get("days") or []) + f" at {at}"


SUPPORTED_SCHEDULES = "once, every day, weekdays, or weekly on chosen days, at one time of day"


def resolve_timing(request, now, steering=()):
    """Resolve timing across the original request and any later steering updates (newer updates override earlier ones)."""
    base = read_timing(request, now)
    base["time_context"] = _timing_text(request)
    for note in steering or ():
        if not isinstance(note, str) or not note.strip():
            continue
        patch = read_timing(note, now)
        if patch["unsupported"]:
            base["unsupported"] = patch["unsupported"]
            base["problem"] = None
            continue
        if patch["problem"]:
            base["problem"] = patch["problem"]
            continue
        has_cadence = bool(patch["kind"] or patch["days"] or patch["date"])
        has_time = bool(patch["time"] or patch["ambiguous_hour"])
        if not has_cadence and not has_time:
            continue
        # A valid steered update supersedes an earlier unsupported/problematic timing.
        if has_cadence:
            base["unsupported"] = None
        if has_cadence or (has_time and base["kind"] and not base["unsupported"]):
            base["problem"] = None
        if has_cadence:
            # Steering a weekly schedule with a bare day name ("change it to Friday instead") keeps it weekly.
            patch_days = _day_mentions(_timing_text(note))
            if (base["kind"] == "weekly" and patch["kind"] == "once" and patch_days
                    and not _EXPLICIT_ONCE.search(note) and not _MONTH_DAY.search(note) and not _IN_OFFSET.search(note)):
                base["days"] = sorted({day for day, _, _ in patch_days})
                base["date"] = None
                base["flags"].difference_update({"kind", "days", "date"})
            else:
                base["kind"] = patch["kind"]
                base["days"] = patch["days"]
                base["date"] = patch["date"]
                base["flags"].difference_update({"kind", "days", "date"})
                base["flags"].update(patch["flags"] & {"kind", "days", "date"})
        if has_time:
            base["time"] = patch["time"]
            base["ambiguous_hour"] = patch["ambiguous_hour"]
            base["time_context"] = _timing_text(note)
            base["flags"].discard("time")
            if "time" in patch["flags"]:
                base["flags"].add("time")
    return base


def resolve_permissions(request, steering=()):
    """Resolve create-files and web permissions chronologically so steering can turn either on or off."""
    create = False
    web = False
    for text in [request, *(steering or ())]:
        if not isinstance(text, str) or not text.strip():
            continue
        if _NO_CREATE.search(text):
            create = False
        elif _CREATE_FILE.search(text):
            create = True
        if _NO_WEB.search(text):
            web = False
        elif _WEB.search(text):
            web = True
    return ("create" if create else "read"), web


def proposal_from_tool(args, now, request="", steering=()):
    """Turn schedule_task arguments into a validated draft task for the confirmation card. Nothing is saved here.

    Returns {"task", "nextRun", "needsClarification", "problem"} or {"unsupported": reason}. Raises ValueError when
    the arguments can't describe a task at all."""
    if not isinstance(args, dict):
        raise ValueError("schedule_task needs its arguments as an object.")
    request = request if isinstance(request, str) else ""
    steering_list = [str(item) for item in (steering or ()) if isinstance(item, str) and item.strip()]
    timing = resolve_timing(request, now, steering_list)
    if timing["unsupported"]:
        return {"unsupported": timing["unsupported"]}
    if timing["problem"]:
        return {"problem": timing["problem"]}
    flags = set(timing["flags"])
    today = now.date()
    kind = timing["kind"]
    if kind is None and (timing["time"] or timing["ambiguous_hour"]):
        kind = "once"
    if kind is None:
        kind = args.get("repeat") if args.get("repeat") in KINDS else None
    if kind is None:
        raise ValueError(f"Say how often it runs: {SUPPORTED_SCHEDULES}.")
    if timing["kind"] is None:
        flags.add("kind")
    schedule = {"kind": kind}
    if kind == "weekly":
        days = timing["days"] or _model_days(args.get("days"))
        if not timing["days"]:
            flags.add("days")
        schedule["days"] = days or [today.weekday()]
    if kind == "once":
        date = (datetime.date.fromisoformat(timing["date"]) if timing["date"] else None) or _model_date(args.get("date"), today)
        if not timing["date"]:
            flags.add("date")
        schedule["date"] = (date or today).isoformat()
    model_time = _model_time(args.get("time"))
    if timing["time"]:
        schedule["time"] = timing["time"]
    elif timing["ambiguous_hour"]:
        hour, minute = timing["ambiguous_hour"]
        time_ctx = timing.get("time_context") or _timing_text(request)
        hour, sure = _resolve_hour(hour, time_ctx, model_time[0] if model_time else None)
        if not sure and steering_list:
            # Fall back to original request's morning/evening cue if steering only changed the hour.
            hour2, sure2 = _resolve_hour(timing["ambiguous_hour"][0], _timing_text(request), model_time[0] if model_time else None)
            if sure2:
                hour, sure = hour2, True
        schedule["time"] = f"{hour:02d}:{minute:02d}"
        if not sure:
            flags.add("time")
    elif model_time and not request and not steering_list:
        schedule["time"] = f"{model_time[0]:02d}:{model_time[1]:02d}"
    else:
        # The user didn't say a time in this message: a time from the model is a guess (or from earlier in the chat).
        schedule["time"] = f"{model_time[0]:02d}:{model_time[1]:02d}" if model_time else "09:00"
        flags.add("time")
    prompt = args.get("prompt") if isinstance(args.get("prompt"), str) and args.get("prompt").strip() else _strip_timing(request)
    access, web = resolve_permissions(request, steering_list)
    task = clean_task({
        "title": args.get("title") if isinstance(args.get("title"), str) else "",
        "prompt": prompt or request,
        "schedule": schedule,
        # The user's own words (with later steering overriding earlier) decide new files and web search.
        "access": access,
        "web": web,
        "enabled": True,
    })
    task.pop("id", None)
    task.pop("created", None)
    problem = None
    upcoming = next_run(task["schedule"], now)
    if upcoming is None and kind == "once":
        if not timing["date"] and "date" in flags:
            # "at 9am" said after 9am: the next 9am is tomorrow.
            task["schedule"]["date"] = (today + datetime.timedelta(days=1)).isoformat()
            upcoming = next_run(task["schedule"], now)
        elif task["schedule"]["date"] < today.isoformat():
            problem = "That date has already passed."
        else:
            problem = "That time has already passed today."
    if upcoming and upcoming.date() > today + datetime.timedelta(days=366):
        problem, upcoming = "That date is more than a year away.", None
    order = ("kind", "days", "date", "time")
    return {"task": task, "nextRun": iso(upcoming), "problem": problem,
            "needsClarification": [field for field in order if field in flags]}


_FILE_NOUN = r"(?:files?|documents?|spreadsheets?|pdfs?|csvs?|workbooks?)"
_FILE_EXT = r"[\w./-]+\.(?:md|txt|html?|docx|pptx|pdf|csv|json)\b"
_FILE_MODIFIERS = r"(?:(?:a|an|the|new|another|separate|dated|daily|weekly|summary|text|markdown|word|pdf|csv|excel|output)\s+)*"
_CREATE_FILE = re.compile(
    rf"\b(?:write|save|create|generate|export|make|produce|draft)\s+{_FILE_MODIFIERS}(?:{_FILE_NOUN}|{_FILE_EXT})|"
    rf"\b(?:write|save|create|generate|export|produce)\b(?:[^.!?\n]|\.(?=\w)){{0,100}}\b(?:to|as|into)\s+{_FILE_MODIFIERS}(?:{_FILE_NOUN}|{_FILE_EXT})|"
    r"\b(?:save|export|write)\b(?:[^.!?\n]|\.(?=\w)){0,100}\b(?:in|to|into)\s+(?:(?:a|the|my|this)\s+)?folder\b", re.I)
_WEB = re.compile(r"\b(?:search|browse|check|look up|research|fetch|read|monitor|visit|use)\b(?:[^.!?\n]|\.(?=\w)){0,80}"
                  r"(?:\b(?:the web|the internet|online|websites?|web pages?)\b|https?://)", re.I)
_NO_CREATE = re.compile(r"\b(?:read[- ]only|(?:do not|don't|never|without)\s+"
                        r"(?:(?:[^.!?\n]|\.(?=\w)){0,100}\b(?:or|and)\s+)?(?:write|save|create|generate|modify|edit|change))\b", re.I)
_NO_WEB = re.compile(r"\b(?:offline|no (?:web|internet)|(?:do not|don't|never|without)\s+"
                     r"(?:(?:[^.!?\n]|\.(?=\w)){0,100}\b(?:or|and)\s+)?(?:browse|search|use)\s+(?:the )?(?:web|internet))\b", re.I)


def iso(moment):
    return moment.isoformat(timespec="seconds") if moment else None


def parse_iso(text):
    try:
        return datetime.datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def format_clock_human(hhmm):
    """Turn '18:30' into '6:30 PM' without platform-specific strftime flags."""
    hour, minute = parse_clock(hhmm)
    ampm = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {ampm}"


def describe_schedule_human(schedule):
    """Plain-English schedule phrase for conversational chat replies."""
    if not isinstance(schedule, dict) or not schedule.get("time"):
        return ""
    at = format_clock_human(schedule["time"])
    kind = schedule.get("kind")
    if kind == "daily":
        return f"every day at {at}"
    if kind == "weekdays":
        return f"on weekdays at {at}"
    if kind == "weekly":
        days = [FULL_DAY_NAMES[day] for day in (schedule.get("days") or []) if isinstance(day, int) and 0 <= day <= 6]
        if not days:
            return f"every week at {at}"
        if len(days) == 1:
            day_phrase = days[0]
        elif len(days) == 2:
            day_phrase = f"{days[0]} and {days[1]}"
        else:
            day_phrase = ", ".join(days[:-1]) + f", and {days[-1]}"
        return f"every {day_phrase} at {at}"
    if kind == "once":
        try:
            date = datetime.date.fromisoformat(schedule.get("date") or "")
        except ValueError:
            return f"once at {at}"
        return f"once on {date.strftime('%a, %b')} {date.day} at {at}"
    return at


def format_moment_human(iso_text, now=None):
    """Turn an ISO local timestamp into 'today at 6:30 PM', 'tomorrow at 9:00 AM', or 'Wed, Dec 2 at 6:30 PM'."""
    moment = parse_iso(iso_text)
    if moment is None:
        return ""
    ref = now or datetime.datetime.now()
    at = format_clock_human(f"{moment.hour:02d}:{moment.minute:02d}")
    if moment.date() == ref.date():
        return f"today at {at}"
    if moment.date() == ref.date() + datetime.timedelta(days=1):
        return f"tomorrow at {at}"
    year_part = f", {moment.year}" if moment.year != ref.year else ""
    return f"{moment.strftime('%a, %b')} {moment.day}{year_part} at {at}"


def parse_clock(text):
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        raise ValueError("Choose a time.")
    if not (text[:2].isascii() and text[:2].isdigit() and text[3:].isascii() and text[3:].isdigit()):
        raise ValueError("Choose a valid time.")
    hour, minute = int(text[:2]), int(text[3:])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("Choose a valid time.")
    return hour, minute


def next_run(schedule, after):
    """The first moment strictly after `after` (local, naive) when the schedule fires, or None when it never will."""
    hour, minute = parse_clock(schedule["time"])
    at_time = datetime.time(hour, minute)
    if schedule["kind"] == "once":
        moment = datetime.datetime.combine(datetime.date.fromisoformat(schedule["date"]), at_time)
        return moment if moment > after else None
    days = {"daily": range(7), "weekdays": range(5)}.get(schedule["kind"], schedule.get("days") or [])
    for offset in range(8):
        day = after.date() + datetime.timedelta(days=offset)
        if day.weekday() in days:
            moment = datetime.datetime.combine(day, at_time)
            if moment > after:
                return moment
    return None


def clean_schedule(raw):
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise ValueError("Choose how often the task runs.")
    schedule = {"kind": raw["kind"], "time": raw.get("time")}
    parse_clock(schedule["time"])
    if schedule["kind"] == "once":
        try:
            schedule["date"] = datetime.date.fromisoformat(raw.get("date")).isoformat()
        except (TypeError, ValueError):
            raise ValueError("Choose a date for the one-time run.") from None
    if schedule["kind"] == "weekly":
        days = raw.get("days")
        if (not isinstance(days, list) or not days or len(days) > 7
                or not all(type(day) is int and 0 <= day <= 6 for day in days)):
            raise ValueError("Choose at least one day of the week.")
        schedule["days"] = sorted(set(days))
    return schedule


def clean_task_skill(raw):
    """Validate an optional skill attached to a scheduled task (text instructions + declarative checks only)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Invalid skill for scheduled task.")
    name_raw = raw.get("name")
    if not isinstance(name_raw, str):
        raise ValueError("Invalid skill name.")
    name = name_raw.strip().lstrip("/").lower()
    if not skills_module.NAME_RE.fullmatch(name):
        raise ValueError("Invalid skill name.")
    instructions = raw.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > PROMPT_LIMIT:
        raise ValueError("Invalid skill instructions.")
    checks = skills_module.normalize_checks(raw.get("checks"))
    output = str(raw.get("output") or "").strip().lower()
    if output not in skills_module.ALLOWED_OUTPUTS:
        output = ""
    out = {"name": name, "instructions": instructions.strip()}
    if checks:
        out["checks"] = checks
    if output:
        out["output"] = output
    return out


def clean_task(raw, existing=None):
    """Validate a task from the page. The folder is filled in by the server from an authorized workspace."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid task.")
    skill = clean_task_skill(raw.get("skill")) if "skill" in raw else (existing or {}).get("skill")
    title = raw.get("title")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        prompt = ""
    if not prompt.strip() and not skill:
        raise ValueError("Describe what the task should do.")
    if len(prompt) > PROMPT_LIMIT:
        raise ValueError(f"Keep the instructions under {PROMPT_LIMIT:,} characters.")
    if not isinstance(title, str) or not title.strip():
        fallback = f"/{skill['name']}" + (f" {prompt.strip()}" if prompt.strip() else "") if skill else prompt
        title = " ".join(fallback.split())[:60]
    title = " ".join(title.split())[:TITLE_LIMIT]
    access = raw.get("access", "read")
    if access not in ACCESS:
        raise ValueError("Choose what the task may do.")
    for flag in ("web", "enabled"):
        if not isinstance(raw.get(flag, True), bool):
            raise ValueError("Invalid task.")
    task = dict(existing or {})
    task.update(title=title, prompt=prompt.strip(), schedule=clean_schedule(raw.get("schedule")), access=access,
                web=raw.get("web", False), enabled=raw.get("enabled", True))
    if skill:
        task["skill"] = skill
    else:
        task.pop("skill", None)
    task.setdefault("id", uuid.uuid4().hex)
    task.setdefault("created", iso(datetime.datetime.now()))
    return task


class ProcessLock:
    """Only one Pipkin server on this computer runs scheduled tasks (the dev window runs a second server)."""

    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                handle = open(self.path, "a+b")
            else:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                handle = os.fdopen(fd, "r+b")
        except OSError:
            return False
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True


class Store:
    """Tasks and the inbox in one private JSON file, written atomically."""

    lock = threading.RLock()  # shared, so every Store for the same file in this process serializes its writes

    def __init__(self, folder):
        self.path = Path(folder) / "schedules.json"

    def load(self):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("version", 1)
            data.setdefault("paused", False)
            data["tasks"] = [task for task in data.get("tasks", []) if isinstance(task, dict) and task.get("id")]
            data["inbox"] = [item for item in data.get("inbox", []) if isinstance(item, dict) and item.get("id")]
            return data

    def save(self, data):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            text = json.dumps(data, ensure_ascii=False, indent=1)
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.path)
            except OSError:
                temp.unlink(missing_ok=True)
                raise

    @contextmanager
    def transaction_lock(self):
        # The dev and installed servers can both edit the same store. Serialize the
        # entire read-modify-write transaction across processes, not just threads.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".write.lock")
        if sys.platform == "win32":
            handle = open(lock_path, "a+b")
        else:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            handle = os.fdopen(fd, "r+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
        finally:
            handle.close()

    def update(self, change):
        with self.lock, self.transaction_lock():
            data = self.load()
            original = json.dumps(data, sort_keys=True)
            result = change(data)
            if json.dumps(data, sort_keys=True) != original:
                self.save(data)
            return result


def find(items, item_id):
    return next((item for item in items if item.get("id") == item_id), None)


class Scheduler:
    """Wakes every half minute, starts due tasks one at a time, and records each outcome in the inbox.

    `run_task(task, stop_event)` does the actual work and returns
    {"status": "complete" | "stopped" | "error" | "busy", "summary": str, "outputs": [...]}."""

    def __init__(self, store, run_task, *, pressure=lambda: "normal", busy=lambda: False,
                 clock=datetime.datetime.now, lock=None):
        self.store = store
        self.run_task = run_task
        self.pressure = pressure
        self.busy = busy
        self.clock = clock
        self.lock = lock
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.manual = []                 # task ids queued with Run now
        self.manual_lock = threading.Lock()
        self.deferred = {}               # task id -> when it first had to wait
        self.running = None
        self.last_tick = None
        self.thread = None

    # ----- control from the HTTP handlers -----

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.loop, name="pipkin-scheduler", daemon=True)
            self.thread.start()

    def run_now(self, task_id):
        with self.manual_lock:
            if task_id not in self.manual and (self.running or {}).get("taskId") != task_id:
                self.manual.append(task_id)
        self.wake.set()

    def stop_current(self):
        if self.running:
            self.stop_event.set()
            return True
        return False

    def poke(self):
        self.wake.set()

    def state(self):
        current = self.running
        running = dict(current) if current else None
        with self.manual_lock:
            waiting = list(self.manual)
        return {"running": running, "queued": waiting,
                "active": bool(self.lock is None or self.lock.handle is not None)}

    # ----- the loop -----

    def loop(self):
        while True:
            try:
                if self.lock is None or self.lock.acquire():
                    self.tick()
            except Exception as error:  # noqa: BLE001 - the scheduler must keep running
                print(f"Scheduler error: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            self.wake.wait(TICK_SECONDS)
            self.wake.clear()

    def tick(self):
        now = self.clock()
        self.last_tick = now
        due = self.store.update(lambda data: self.plan(data, now))
        with self.manual_lock:
            manual, self.manual = self.manual, []
        for task_id in manual:
            # A manual run of an already-due task satisfies that occurrence.
            trigger = "schedule" if task_id in due else "manual"
            self.execute(task_id, trigger=trigger)
            if task_id in due:
                due.remove(task_id)
        for task_id in due:
            self.execute(task_id, trigger="schedule")

    def plan(self, data, now):
        """Advance every task's next run; return the ids that should start now, oldest first."""
        due = []
        for task in data["tasks"]:
            schedule = task.get("schedule")
            if not schedule:
                continue
            upcoming = parse_iso(task.get("nextRun"))
            if not task.get("enabled", True):
                task["nextRun"] = None
                task.pop("waitReason", None)
                self.deferred.pop(task["id"], None)
                continue
            if upcoming is None:
                upcoming = next_run(schedule, max(now, parse_iso(task.get("created")) or now) - datetime.timedelta(seconds=1))
                task["nextRun"] = iso(upcoming)
                if upcoming is None:
                    task["enabled"] = False
                    continue
            if upcoming > now:
                continue
            if data.get("paused"):
                # While intentionally paused, quietly roll past expired occurrences instead of flagging them "missed while closed".
                task.pop("waitReason", None)
                self.deferred.pop(task["id"], None)
                if now - upcoming > MISSED_GRACE:
                    self.advance(task, now)
                continue
            waiting_since = self.deferred.get(task["id"])
            if waiting_since is None and now - upcoming > MISSED_GRACE:
                # Due while Pipkin was closed or asleep: offer it instead of starting work the user didn't expect.
                task["missed"] = iso(upcoming)
                self.advance(task, now)
                continue
            if waiting_since is not None and now - waiting_since > DEFER_LIMIT:
                self.deferred.pop(task["id"], None)
                reason = task.pop("waitReason", None) or "the model was busy"
                self.add_inbox(data, task, "skipped", f"Skipped because {reason} for more than 45 minutes.", [], now, now,
                               "schedule")
                self.advance(task, now)
                continue
            # Tasks already queued behind another task must not become missed.
            self.deferred.setdefault(task["id"], now)
            due.append((upcoming, task["id"]))
        return [task_id for _, task_id in sorted(due)]

    def advance(self, task, now):
        upcoming = next_run(task["schedule"], now)
        task["nextRun"] = iso(upcoming)
        if upcoming is None:
            task["enabled"] = False

    def execute(self, task_id, *, trigger):
        data = self.store.load()
        task = find(data["tasks"], task_id)
        if task is None:
            return
        now = self.clock()
        if trigger == "schedule":
            upcoming = parse_iso(task.get("nextRun"))
            if data.get("paused") or not task.get("enabled", True) or upcoming is None or upcoming > now:
                self.deferred.pop(task_id, None)
                return
        wait = "the model was busy" if self.busy() else "memory pressure was critical" if self.pressure() == "critical" else None
        if wait:
            if trigger == "manual":
                self.store.update(lambda d: self.add_inbox(
                    d, task, "skipped", f"Not started because {wait}. Try Run now again in a moment.", [], now, now, trigger))
            else:
                self.deferred.setdefault(task_id, now)
                self.store.update(lambda d: (find(d["tasks"], task_id) or {}).update(waitReason=wait))
            return
        self.stop_event.clear()
        self.running = {"taskId": task_id, "title": task["title"], "startedAt": iso(now), "trigger": trigger}
        try:
            result = self.run_task(dict(task), self.stop_event)
        except Exception as error:  # noqa: BLE001 - one broken run must not stop the scheduler
            result = {"status": "error", "summary": f"Pipkin hit an unexpected error ({type(error).__name__}).",
                      "outputs": []}
        finally:
            self.running = None
        ended = self.clock()
        if result.get("status") == "busy":
            if trigger == "manual":
                self.store.update(lambda d: self.add_inbox(
                    d, task, "skipped", "Not started because the model was busy. Try Run now again in a moment.", [],
                    now, ended, trigger))
            else:
                self.deferred.setdefault(task_id, now)
                self.store.update(lambda d: (find(d["tasks"], task_id) or {}).update(waitReason="the model was busy"))
            return
        self.deferred.pop(task_id, None)
        status = result.get("status") if result.get("status") in ("complete", "stopped", "error") else "error"

        def record(d):
            current = find(d["tasks"], task_id)
            if current is not None:
                current.pop("waitReason", None)
                current["lastRun"] = iso(ended)
                current["lastStatus"] = status
                # The task may have been edited, disabled or rescheduled while it ran.
                if current.get("revision") == task.get("revision") and current.get("nextRun") == task.get("nextRun"):
                    current.pop("missed", None)
                    if trigger == "schedule" and current.get("enabled", True):
                        self.advance(current, ended)
            self.add_inbox(d, task, status, result.get("summary") or "", result.get("outputs") or [],
                           now, ended, trigger, undo_port=result.get("undoPort"),
                           skill_checks=result.get("skillChecks"))
        self.store.update(record)

    @staticmethod
    def add_inbox(data, task, status, summary, outputs, started, ended, trigger, *, undo_port=None, skill_checks=None):
        item = {
            "id": uuid.uuid4().hex, "taskId": task.get("id"), "title": task.get("title", "Scheduled task"),
            "status": status, "summary": summary[:20_000], "outputs": outputs[:40], "folder": task.get("folder"),
            "access": task.get("access"), "startedAt": iso(started), "endedAt": iso(ended), "trigger": trigger,
            "read": False,
        }
        if isinstance(undo_port, int) and 1 <= undo_port <= 65535:
            item["undoPort"] = undo_port
        if isinstance(skill_checks, dict) and isinstance(skill_checks.get("results"), list) and skill_checks["results"]:
            item["skillChecks"] = skill_checks
        data["inbox"].insert(0, item)
        del data["inbox"][INBOX_LIMIT:]
        return item
