from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import re


WEEKDAYS = {
    "ponedeljak": 0,
    "utorak": 1,
    "sredu": 2,
    "sreda": 2,
    "cetvrtak": 3,
    "četvrtak": 3,
    "petak": 4,
    "subotu": 5,
    "subota": 5,
    "nedelju": 6,
    "nedelja": 6,
}


@dataclass(frozen=True)
class ParsedItem:
    title: str
    type: str
    date: datetime | None
    project: str = ""
    repeat: str = "None"
    next_check: datetime | None = None
    check_mode: str = "Reminder"
    location: str = ""
    priority: str = "Normal"
    source: str = "Telegram"
    result: str = ""
    score: int | None = None
    raw: str = ""


def _next_weekday(today: date, weekday: int) -> date:
    days = (weekday - today.weekday()) % 7
    if days == 0:
        days = 7
    return today + timedelta(days=days)


def _parse_time(text: str) -> tuple[time | None, str]:
    patterns = [
        r"\bu\s+(\d{1,2})(?::(\d{2}))?\s*h?\b",
        r"\bod\s+(\d{1,2})(?::(\d{2}))?\s*h?\b",
        r"\b(\d{1,2}):(\d{2})\b",
        r"\b(\d{1,2})h\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            cleaned = (text[: match.start()] + text[match.end() :]).strip()
            return time(hour, minute), re.sub(r"\s+", " ", cleaned)
    return None, text


def _parse_date(text: str, now: datetime) -> tuple[date | None, str]:
    lowered = text.lower()
    if "sutra" in lowered:
        return now.date() + timedelta(days=1), re.sub(r"\bsutra\b", "", text, flags=re.IGNORECASE).strip()
    if "danas" in lowered:
        return now.date(), re.sub(r"\bdanas\b", "", text, flags=re.IGNORECASE).strip()

    explicit = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\b", text)
    if explicit:
        day, month, year = map(int, explicit.groups())
        cleaned = (text[: explicit.start()] + text[explicit.end() :]).strip()
        return date(year, month, day), re.sub(r"\s+", " ", cleaned)

    for word, weekday in WEEKDAYS.items():
        match = re.search(rf"\b{word}\b", lowered)
        if match:
            cleaned = (text[: match.start()] + text[match.end() :]).strip()
            return _next_weekday(now.date(), weekday), re.sub(r"\s+", " ", cleaned)
    return None, text


def _project_from_title(title: str) -> str:
    known = ["JuristSoft", "ellco.pro", "Gmail", "Telegram", "Mac"]
    lowered = title.lower()
    for project in known:
        if project.lower() in lowered:
            return project
    dotted = re.search(r"\b[\w-]+\.[\w.-]+\b", title)
    return dotted.group(0) if dotted else ""


def parse_serbian(text: str, timezone: str = "Europe/Belgrade", now: datetime | None = None) -> ParsedItem:
    tz = ZoneInfo(timezone)
    current = now.astimezone(tz) if now else datetime.now(tz)
    raw = text.strip()
    lowered = raw.lower()

    if lowered.startswith("beleška:") or lowered.startswith("beleska:"):
        title = raw.split(":", 1)[1].strip()
        parsed_date, title = _parse_date(title, current)
        return ParsedItem(
            title=title,
            type="Note",
            date=datetime.combine(parsed_date, current.time(), tzinfo=tz) if parsed_date else current,
            project=_project_from_title(title),
            raw=raw,
        )

    if lowered.startswith("plan za"):
        title = raw.split(":", 1)[1].strip() if ":" in raw else raw
        parsed_date, _ = _parse_date(raw, current)
        return ParsedItem(
            title=title,
            type="Plan",
            date=datetime.combine(parsed_date or current.date(), time(9, 0), tzinfo=tz),
            project=_project_from_title(title),
            raw=raw,
        )

    if lowered.startswith(("zadatak:", "task:", "todo:")):
        raw = raw.split(":", 1)[1].strip()

    parsed_time, without_time = _parse_time(raw)
    parsed_date, without_date = _parse_date(without_time, current)
    item_date = None
    if parsed_date:
        item_date = datetime.combine(parsed_date, parsed_time or time(9, 0), tzinfo=tz)

    title = re.sub(r"\s+", " ", without_date).strip(" ,-")
    title = re.sub(r"^u\s+", "", title, flags=re.IGNORECASE).strip()
    return ParsedItem(
        title=title or raw,
        type="Task",
        date=item_date,
        project=_project_from_title(title),
        raw=raw,
    )
