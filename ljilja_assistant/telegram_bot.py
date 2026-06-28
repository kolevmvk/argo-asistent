from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo
import calendar
import json
import re
import time as sleep_time
import traceback
import urllib.error
import urllib.parse
import urllib.request

from .config import Config
from .jira_client import JiraClient
from .mac_status import get_mac_status
from .notion_client import NotionClient, memory_line, page_check_mode, page_next_check, page_repeat, page_status, page_title, summarize_page
from .ollama_client import OllamaClient
from .parser import ParsedItem, parse_serbian


ACTION_TO_STATUS = {
    "done": "Done",
    "partial": "Partial",
    "tomorrow": "Moved",
    "skip": "Skipped",
    "skip_today": "Skipped",
}

QUESTION_STARTS = (
    "ko ",
    "šta ",
    "sta ",
    "kako ",
    "zašto ",
    "zasto ",
    "gde ",
    "gdje ",
    "kad ",
    "kada ",
    "koliko ",
    "možeš ",
    "mozes ",
    "možeš li ",
    "mozes li ",
    "da li ",
    "jesi li ",
    "imaš li ",
    "imas li ",
)

TASK_PREFIXES = ("zadatak:", "task:", "todo:", "beleška:", "beleska:", "plan za")
DATE_HINTS = (
    "danas",
    "sutra",
    "ponedeljak",
    "utorak",
    "sredu",
    "sreda",
    "cetvrtak",
    "četvrtak",
    "petak",
    "subotu",
    "subota",
    "nedelju",
    "nedelja",
)

REMINDER_WORDS = ("podseti", "podsetime", "podsjeti", "seti me", "sjeti me", "podsećaj", "podsecaj", "proveravaj")
MEMORY_WORDS = ("zapamti", "upamti", "zabeleži", "zabelezi", "zapisi", "zapiši", "imaj u vidu")
TASK_WORDS = ("zadatak", "task", "todo", "rok", "deadline", "uradi", "završi", "zavrsi", "treba")
WEEKDAY_NAMES = {
    "ponedeljak": 0,
    "ponedeljka": 0,
    "utorak": 1,
    "utorka": 1,
    "sreda": 2,
    "sredu": 2,
    "srede": 2,
    "četvrtak": 3,
    "cetvrtak": 3,
    "četvrtka": 3,
    "cetvrtka": 3,
    "petak": 4,
    "petka": 4,
    "subota": 5,
    "subotu": 5,
    "subote": 5,
    "nedelja": 6,
    "nedelju": 6,
    "nedelje": 6,
}
PRESENCE_PATTERNS = (
    r"\bjesi\s+tu\b",
    r"\bsi\s+tu\b",
    r"\btu\s+si\b",
    r"\bima\s+li\s+te\b",
    r"\bčuješ\s+li\b",
    r"\bcujes\s+li\b",
)
NUMBER_WORDS = {
    "jedan": 1,
    "jednu": 1,
    "dva": 2,
    "dve": 2,
    "tri": 3,
    "cetiri": 4,
    "četiri": 4,
    "pet": 5,
    "sest": 6,
    "šest": 6,
    "sedam": 7,
    "osam": 8,
    "devet": 9,
    "deset": 10,
    "petnaest": 15,
    "dvadeset": 20,
    "trideset": 30,
}


def _add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


class TelegramBot:
    def __init__(self, config: Config):
        self.config = config
        self.notion = NotionClient(config)
        self.jira = JiraClient(config)
        self.ollama = OllamaClient(config)
        self.offset = 0
        self.last_daily_report_date: str | None = None
        self.last_created_pages: dict[str, tuple[str, str]] = {}
        self.recent_turns: dict[str, list[str]] = {}
        self.pending_link_items: dict[str, ParsedItem] = {}
        self.api_url = f"https://api.telegram.org/bot{config.telegram_bot_token}"

    def _api(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = urllib.parse.urlencode(payload or {}).encode("utf-8")
        request = urllib.request.Request(f"{self.api_url}/{method}", data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API error {exc.code}: {detail}") from exc

    def send_message(self, chat_id: str, text: str, page_id: str | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if page_id:
            payload["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [
                        [
                            {"text": "Urađeno", "callback_data": f"done:{page_id}"},
                            {"text": "Kasnije", "callback_data": f"snooze10:{page_id}"},
                        ],
                        [
                            {"text": "Sutra", "callback_data": f"tomorrow:{page_id}"},
                            {"text": "Preskoči danas", "callback_data": f"skip_today:{page_id}"},
                        ],
                    ]
                },
                ensure_ascii=False,
            )
        self._api("sendMessage", payload)

    def _allowed(self, chat_id: Any) -> bool:
        return str(chat_id) == str(self.config.telegram_allowed_chat_id)

    def _dry_run_text(self, item: ParsedItem) -> str:
        when = item.date.isoformat() if item.date else "bez datuma"
        return (
            "DRY_RUN=true, ne upisujem u Notion.\n"
            f"Upisao bih: {item.title}\n"
            f"Type: {item.type}\nProject: {item.project or '-'}\nDate: {when}\nStatus: Planned"
        )

    def _daily_report_due(self, now: datetime) -> bool:
        hour, minute = map(int, self.config.daily_report_time.split(":", 1))
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        today_key = now.date().isoformat()
        return now >= scheduled and self.last_daily_report_date != today_key

    def send_daily_report_if_due(self) -> None:
        tz = ZoneInfo(self.config.timezone)
        now = datetime.now(tz)
        if not self._daily_report_due(now):
            return
        self.send_message(self.config.telegram_allowed_chat_id, self.report_text())
        self.last_daily_report_date = now.date().isoformat()

    def send_due_reminders(self) -> None:
        if self.config.dry_run or not self.config.notion_enabled:
            return
        now = datetime.now(ZoneInfo(self.config.timezone))
        for page in self.notion.query_due(now):
            page_id = page.get("id", "")
            title = page_title(page)
            self.send_message(self.config.telegram_allowed_chat_id, f"Podsetnik: {title}", page_id=page_id)
            if page_id:
                self.notion.update_status(page_id, "In Progress")

    @staticmethod
    def _next_recurrence(now: datetime, repeat: str) -> datetime:
        candidate = now + timedelta(days=1)
        if repeat == "Weekdays":
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        elif repeat == "Weekly":
            candidate = now + timedelta(days=7)
        elif repeat == "Monthly":
            candidate = _add_months(now, 1)
        elif repeat == "Yearly":
            candidate = _add_months(now, 12)
        return candidate

    def send_due_checks(self) -> None:
        if self.config.dry_run or not self.config.notion_enabled:
            return
        now = datetime.now(ZoneInfo(self.config.timezone))
        for page in self.notion.query_due_checks(now):
            page_id = page.get("id", "")
            title = page_title(page)
            mode = page_check_mode(page)
            repeat = page_repeat(page)
            prefix = "Da li je urađeno" if mode == "AskUntilDone" else "Podsetnik"
            self.send_message(self.config.telegram_allowed_chat_id, f"{prefix}: {title}", page_id=page_id)
            if page_id:
                scheduled = page_next_check(page) or now
                self.notion.reschedule_check(page_id, self._next_recurrence(scheduled, repeat), "Planned")

    @staticmethod
    def _strip_address(text: str) -> str:
        return re.sub(r"^\s*ljilj[ao]?\s*,?\s+", "", text.strip(), flags=re.IGNORECASE)

    @staticmethod
    def _clean_reminder_title(text: str) -> str:
        cleaned = TelegramBot._strip_address(text)
        cleaned = re.sub(r"\b(podsetime|podseti|podsjeti|seti me|sjeti me|zapamti)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(podsećaj|podsecaj|proveravaj)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bsvakog\s+(ponedeljka|utorka|srede|četvrtka|cetvrtka|petka|subote|nedelje)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bsvakog\s+\d{1,2}\.\d{1,2}\.?\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bsvakog\s+\d{1,2}\.?\s*(?:u\s+mesecu)?\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bme\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bsvaki\s+dan\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bradnim\s+danima\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\b(za|kroz)\s+(\d{1,3}|jedan|jednu|dva|dve|tri|cetiri|četiri|pet|sest|šest|sedam|osam|devet|deset|petnaest|dvadeset|trideset)\s+"
            r"(minut|minuta|minute|sat|sata|sati)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\bda\s+li\b", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(sam|si|je|smo|ste|su)\s+", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(da|na)\b", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(danas|sutra|stra)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bu\s+\d{1,2}(?::\d{2})?\s*h?\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", "", cleaned)
        cleaned = re.sub(r"\b\d{1,2}h\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(sam|si|je|smo|ste|su)\s+", "", cleaned, count=1, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip(" ,.-")

    @staticmethod
    def _clean_task_title(text: str) -> str:
        cleaned = TelegramBot._strip_address(text)
        cleaned = re.sub(r"^(zadatak|task|todo)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(rok|deadline)\s+(je\s+)?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(do|dok)\s+(danas|sutra|ponedeljka|ponedeljak|utorka|utorak|srede|sreda|četvrtka|cetvrtka|četvrtak|cetvrtak|petka|petak|subote|subota|nedelje|nedelja)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(do|dok)\s+\d{1,2}\.\d{1,2}(?:\.\d{4})?\.?\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(da|treba\s+da)\b", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bu\s+\d{1,2}(?::\d{2})?\s*h?\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", "", cleaned)
        cleaned = re.sub(r"\b\d{1,2}h\b", "", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip(" ,.-")

    @staticmethod
    def _clean_memory_title(text: str) -> str:
        cleaned = TelegramBot._strip_address(text)
        cleaned = re.sub(r"^(zapamti|upamti|zabeleži|zabelezi|zapisi|zapiši|imaj u vidu)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(da|ovo|to|o meni da)\s+", "", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip(" ,.-")

    @staticmethod
    def _correction_text(text: str) -> str | None:
        normalized = TelegramBot._strip_address(text).strip()
        lowered = normalized.lower()
        if not lowered:
            return None

        replacement = re.search(r"\b(?:nije|ne)\s+.+?\s+nego\s+(.+)$", normalized, flags=re.IGNORECASE)
        if replacement:
            return replacement.group(1).strip(" .,-")

        if not re.match(r"^(ispravi|ispravi\s+ovo|ispravi\s+to|ispravi\s+poslednje|promeni|promijeni|prepravi|koriguj)\b", lowered):
            return None

        cleaned = re.sub(
            r"^(ispravi|promeni|promijeni|prepravi|koriguj)\s*(ovo|to|poslednje|zadnje|unos|stavku|task|zadatak)?\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        ).strip(" .,-")
        cleaned = re.sub(r"^(u|na|kao|da bude)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .,-")
        return cleaned

    @staticmethod
    def _number_value(value: str) -> int | None:
        if value.isdigit():
            return int(value)
        return NUMBER_WORDS.get(value.lower())

    def _relative_datetime(self, text: str) -> datetime | None:
        normalized = self._strip_address(text).lower()
        number_pattern = "|".join(sorted((re.escape(word) for word in NUMBER_WORDS), key=len, reverse=True))
        match = re.search(
            rf"\b(?:za|kroz)\s+(\d{{1,3}}|{number_pattern})\s+(minut|minuta|minute|sat|sata|sati)\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        amount = self._number_value(match.group(1))
        if amount is None:
            return None
        unit = match.group(2).lower()
        delta = timedelta(hours=amount) if unit.startswith("sat") else timedelta(minutes=amount)
        return datetime.now(ZoneInfo(self.config.timezone)) + delta

    def _daily_time_datetime(self, text: str) -> datetime | None:
        normalized = self._strip_address(text).lower()
        match = re.search(r"\b(?:u|od)\s+(\d{1,2})(?::(\d{2}))?\s*h?\b|\b(\d{1,2}):(\d{2})\b|\b(\d{1,2})h\b", normalized)
        if not match:
            return None
        hour = int(match.group(1) or match.group(3) or match.group(5))
        minute = int(match.group(2) or match.group(4) or 0)
        if hour > 23 or minute > 59:
            return None
        now = datetime.now(ZoneInfo(self.config.timezone))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _next_weekday_datetime(self, weekday: int, at_time: time | None = None) -> datetime:
        now = datetime.now(ZoneInfo(self.config.timezone))
        days = (weekday - now.weekday()) % 7
        target_time = at_time or time(9, 0)
        candidate = datetime.combine(now.date() + timedelta(days=days), target_time, tzinfo=now.tzinfo)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    def _time_from_text(self, text: str) -> time | None:
        normalized = self._strip_address(text).lower()
        match = re.search(r"\b(?:u|od)\s+(\d{1,2})(?::(\d{2}))?\s*h?\b|\b(\d{1,2}):(\d{2})\b|\b(\d{1,2})h\b", normalized)
        if not match:
            return None
        hour = int(match.group(1) or match.group(3) or match.group(5))
        minute = int(match.group(2) or match.group(4) or 0)
        if hour > 23 or minute > 59:
            return None
        return time(hour, minute)

    def _date_from_day_month(self, day: int, month: int | None = None) -> date:
        now = datetime.now(ZoneInfo(self.config.timezone))
        if month is None:
            year = now.year
            month = now.month
            day = min(day, calendar.monthrange(year, month)[1])
            candidate = date(year, month, day)
            if candidate < now.date():
                next_month = _add_months(datetime.combine(candidate, time.min, tzinfo=now.tzinfo), 1)
                candidate = next_month.date()
            return candidate
        candidate = date(now.year, month, day)
        if candidate < now.date():
            candidate = date(now.year + 1, month, day)
        return candidate

    def _specific_due_datetime(self, text: str) -> datetime | None:
        normalized = self._strip_address(text).lower()
        at_time = self._time_from_text(text) or time(9, 0)
        if re.search(r"\bdanas\b", normalized):
            now = datetime.now(ZoneInfo(self.config.timezone))
            candidate = datetime.combine(now.date(), at_time, tzinfo=now.tzinfo)
            return candidate if candidate > now else now + timedelta(minutes=1)
        if re.search(r"\b(sutra|stra)\b", normalized):
            now = datetime.now(ZoneInfo(self.config.timezone))
            return datetime.combine(now.date() + timedelta(days=1), at_time, tzinfo=now.tzinfo)
        explicit = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\.?\b", normalized)
        if explicit:
            day = int(explicit.group(1))
            month = int(explicit.group(2))
            year = int(explicit.group(3)) if explicit.group(3) else None
            target = date(year, month, day) if year else self._date_from_day_month(day, month)
            return datetime.combine(target, at_time, tzinfo=ZoneInfo(self.config.timezone))
        for word, weekday in WEEKDAY_NAMES.items():
            if re.search(rf"\b{word}\b", normalized):
                return self._next_weekday_datetime(weekday, at_time)
        return None

    def _recurring_schedule(self, text: str) -> tuple[str, datetime] | None:
        normalized = self._strip_address(text).lower()
        at_time = self._time_from_text(text) or time(9, 0)
        if "svaki dan" in normalized:
            when = self._daily_time_datetime(text) or self._specific_due_datetime("sutra")
            return ("Daily", when)
        if "radnim danima" in normalized or "svaki radni dan" in normalized:
            when = self._daily_time_datetime(text) or self._specific_due_datetime("sutra")
            while when.weekday() >= 5:
                when += timedelta(days=1)
            return ("Weekdays", when)
        weekly = re.search(r"\bsvakog\s+(ponedeljka|utorka|srede|četvrtka|cetvrtka|petka|subote|nedelje)\b", normalized)
        if weekly:
            return ("Weekly", self._next_weekday_datetime(WEEKDAY_NAMES[weekly.group(1)], at_time))
        yearly = re.search(r"\bsvakog\s+(\d{1,2})\.(\d{1,2})\.?\b", normalized)
        if yearly:
            target = self._date_from_day_month(int(yearly.group(1)), int(yearly.group(2)))
            return ("Yearly", datetime.combine(target, at_time, tzinfo=ZoneInfo(self.config.timezone)))
        monthly = re.search(r"\bsvakog\s+(\d{1,2})\.?\s*(?:u\s+mesecu)?\b", normalized)
        if monthly:
            target = self._date_from_day_month(int(monthly.group(1)))
            return ("Monthly", datetime.combine(target, at_time, tzinfo=ZoneInfo(self.config.timezone)))
        return None

    def fast_intent(self, text: str) -> dict[str, Any] | None:
        normalized = self._strip_address(text).lower()
        if not normalized:
            return {"action": "chat", "reply": "Tu sam."}
        if any(re.search(pattern, normalized) for pattern in PRESENCE_PATTERNS):
            return {"action": "chat", "reply": "Tu sam. Reci šta treba."}

        has_memory_word = any(word in normalized for word in MEMORY_WORDS)
        has_date_or_time = self._specific_due_datetime(text) is not None or self._relative_datetime(text) is not None
        if has_memory_word and not has_date_or_time:
            title = self._clean_memory_title(text)
            if not title:
                return {"action": "clarify", "reply": "Šta želiš da zapamtim?"}
            return {
                "action": "create_item",
                "type": "Note",
                "title": title,
                "date_iso": None,
                "project": "",
                "repeat": "None",
                "check_mode": "Reminder",
            }

        duty_match = re.search(r"\b(dežuran|dezuran|dežurstvo|dezurstvo)\b", normalized)
        if duty_match:
            item = parse_serbian(self._strip_address(text), self.config.timezone)
            project = item.project
            location_match = re.search(r"\b(?:u|na)\s+([\w.-]+)\s*$", self._strip_address(text), flags=re.IGNORECASE)
            location = location_match.group(1) if location_match else project
            return {
                "action": "create_item",
                "type": "Duty",
                "title": item.title,
                "date_iso": item.date.isoformat() if item.date else None,
                "project": project,
                "location": location,
                "repeat": "None",
                "check_mode": "Reminder",
            }

        has_reminder_word = any(word in normalized for word in REMINDER_WORDS)
        if has_reminder_word:
            fixed = re.sub(r"\bstra\b", "sutra", text, flags=re.IGNORECASE)
            title = self._clean_reminder_title(fixed)
            if not title:
                if re.search(r"\b(sutra|stra)\b", normalized):
                    return {"action": "clarify", "reply": "Na šta tačno da te podsetim sutra?"}
                return {"action": "clarify", "reply": "Na šta tačno da te podsetim?"}
            relative_when = self._relative_datetime(fixed)
            if relative_when is not None:
                return {
                    "action": "create_item",
                    "type": "Reminder",
                    "title": title,
                    "date_iso": relative_when.isoformat(),
                    "project": "",
                    "repeat": "None",
                    "check_mode": "Reminder",
                }
            repeat = "None"
            recurring_when = None
            recurring = self._recurring_schedule(fixed)
            if recurring is not None:
                repeat, recurring_when = recurring
            item = parse_serbian(title, self.config.timezone)
            if recurring_when is not None:
                item = ParsedItem(
                    title=title,
                    type="Reminder",
                    date=recurring_when,
                    repeat=repeat,
                    next_check=recurring_when,
                    check_mode="AskUntilDone" if "proveravaj" in normalized else "Reminder",
                    raw=text,
                )
            if item.date is None and re.search(r"\b(sutra|stra|danas)\b", normalized):
                item = parse_serbian(fixed, self.config.timezone)
                if title:
                    item = ParsedItem(
                        title=title,
                        type="Task",
                        date=item.date,
                        project=item.project,
                        raw=text,
                    )
            if not item.title or item.title.lower() in {"podseti", "podseti me", "podsetime", "sutra"}:
                return {"action": "clarify", "reply": "Na šta tačno da te podsetim?"}
            if item.date is None:
                return {"action": "clarify", "reply": f"Kada da te podsetim za: {item.title}?"}
            return {
                "action": "create_item",
                "type": "Reminder" if repeat != "None" else "Task",
                "title": item.title,
                "date_iso": item.date.isoformat(),
                "project": item.project,
                "repeat": repeat,
                "next_check_iso": item.date.isoformat() if repeat != "None" else None,
                "check_mode": "AskUntilDone" if "proveravaj" in normalized else "Reminder",
            }
        has_task_word = any(word in normalized for word in TASK_WORDS)
        has_due_phrase = re.search(r"\b(do|dok)\s+(danas|sutra|stra|\d{1,2}\.\d{1,2}|ponedeljka|ponedeljak|utorka|utorak|srede|sreda|četvrtka|cetvrtka|četvrtak|cetvrtak|petka|petak|subote|subota|nedelje|nedelja)\b", normalized)
        if normalized.startswith(TASK_PREFIXES) or has_due_phrase or ("rok" in normalized and has_task_word):
            fixed = re.sub(r"\bstra\b", "sutra", text, flags=re.IGNORECASE)
            title = self._clean_task_title(fixed)
            when = self._specific_due_datetime(fixed)
            parsed = parse_serbian(title, self.config.timezone)
            return {
                "action": "create_item",
                "type": "Task",
                "title": title or parsed.title,
                "date_iso": (when or parsed.date).isoformat() if (when or parsed.date) else None,
                "project": parsed.project,
                "repeat": "None",
                "check_mode": "Reminder",
            }
        return None

    @classmethod
    def is_notion_entry(cls, text: str) -> bool:
        normalized = cls._strip_address(text).lower()
        if normalized.startswith(TASK_PREFIXES):
            return True
        if re.search(r"\b\d{1,2}\.\d{1,2}\.\d{4}\.?\b", normalized):
            return True
        if re.search(r"\b\d{1,2}:\d{2}\b", normalized):
            return True
        if re.search(r"\bu\s+\d{1,2}(?::\d{2})?\s*h?\b", normalized):
            return True
        if re.search(r"\b\d{1,2}h\b", normalized):
            return True
        return any(re.search(rf"\b{re.escape(hint)}\b", normalized) for hint in DATE_HINTS)

    @classmethod
    def is_chat_message(cls, text: str) -> bool:
        normalized = cls._strip_address(text).lower()
        if not normalized:
            return False
        if cls.is_notion_entry(text):
            return False
        if "?" in normalized:
            return True
        return True

    def handle_chat(self, chat_id: str, text: str) -> None:
        try:
            self.send_message(chat_id, "Razmišljam...")
            reply = self.ollama.ask(text, self.assistant_context(chat_id))
            self.send_message(chat_id, reply)
            self._remember_turn(chat_id, f"Korisnik: {text}")
            self._remember_turn(chat_id, f"Ljilja: {reply}")
        except Exception as exc:
            self.send_message(
                chat_id,
                f"Ne mogu trenutno da dobijem odgovor od lokalne Ollame.\n{exc}",
            )

    def _remember_turn(self, chat_id: str, line: str) -> None:
        turns = self.recent_turns.setdefault(str(chat_id), [])
        turns.append(line)
        del turns[:-8]

    def assistant_context(self, chat_id: str = "") -> str:
        chunks: list[str] = []
        turns = self.recent_turns.get(str(chat_id), [])
        if turns:
            chunks.append("Skoriji razgovor:\n" + "\n".join(turns))
        if self.config.dry_run or not self.config.notion_enabled:
            return "\n\n".join(chunks)
        try:
            pages = self.notion.query_assistant_context()
        except Exception:
            return "\n\n".join(chunks)
        if pages:
            chunks.append("Notion memorija:\n" + "\n".join(memory_line(page) for page in pages))
        return "\n\n".join(chunks)

    def classify_intent(self, text: str, context: str = "") -> dict[str, Any]:
        fast = self.fast_intent(text)
        if fast is not None:
            return fast
        tz = ZoneInfo(self.config.timezone)
        now = datetime.now(tz)
        return self.ollama.extract_intent(text, now.isoformat(), self.config.timezone, context)

    def _item_from_intent(self, text: str, intent: dict[str, Any]) -> ParsedItem:
        title = str(intent.get("title") or "").strip()
        date_iso = intent.get("date_iso")
        parsed_date = None
        if isinstance(date_iso, str) and date_iso:
            parsed_date = datetime.fromisoformat(date_iso)
        if not title:
            title = self._strip_address(text)
        return ParsedItem(
            title=title,
            type=str(intent.get("type") or "Task"),
            date=parsed_date,
            project=str(intent.get("project") or ""),
            repeat=str(intent.get("repeat") or "None"),
            next_check=datetime.fromisoformat(intent["next_check_iso"]) if isinstance(intent.get("next_check_iso"), str) and intent.get("next_check_iso") else None,
            check_mode=str(intent.get("check_mode") or "Reminder"),
            location=str(intent.get("location") or ""),
            raw=text,
        )

    def enrich_item_with_memory(self, chat_id: str, item: ParsedItem) -> tuple[ParsedItem, str | None]:
        context = self.assistant_context(chat_id)
        if not context:
            return item, None
        decision = self.ollama.link_memory(
            {
                "title": item.title,
                "type": item.type,
                "project": item.project,
                "date": item.date.isoformat() if item.date else None,
                "raw": item.raw,
            },
            context,
        )
        action = decision.get("action")
        if action == "ask":
            if item.type != "Note":
                return item, None
            question = str(decision.get("question") or "").strip()
            return item, question or "Sa kojim projektom ili temom da povežem ovo?"
        if action != "link":
            return item, None
        project = str(decision.get("project") or item.project or "").strip()
        relation_note = str(decision.get("relation_note") or "").strip()
        if not project and not relation_note:
            return item, None
        enriched = ParsedItem(
            title=item.title,
            type=item.type,
            date=item.date,
            project=project,
            repeat=item.repeat,
            next_check=item.next_check,
            check_mode=item.check_mode,
            location=item.location,
            priority=item.priority,
            source=item.source,
            result=relation_note or item.result,
            score=item.score,
            raw=item.raw,
        )
        return enriched, None

    def handle_item(self, chat_id: str, item: ParsedItem, allow_link: bool = True) -> None:
        if allow_link and not self.config.dry_run and self.config.notion_enabled:
            try:
                item, question = self.enrich_item_with_memory(chat_id, item)
            except Exception:
                question = None
            if question:
                self.pending_link_items[str(chat_id)] = item
                self.send_message(chat_id, question)
                return
        if self.config.dry_run:
            self.send_message(chat_id, self._dry_run_text(item))
            self.last_created_pages[str(chat_id)] = ("DRY_RUN", item.title)
            self._remember_turn(chat_id, f"Korisnik je tražio unos: {item.raw or item.title}")
            self._remember_turn(chat_id, f"Ljilja je pripremila {item.type}: {item.title}")
            return
        if not self.config.notion_enabled:
            self.send_message(chat_id, "Notion nije podešen. Popuni NOTION_TOKEN i NOTION_DATABASE_ID ili uključi DRY_RUN=true.")
            return
        created = self.notion.create_item(item)
        page_id = str(created.get("id") or "")
        if page_id:
            self.last_created_pages[str(chat_id)] = (page_id, item.title)
        prefix = "Zapamćeno u Notionu" if item.type == "Note" else "Upisano u Notion"
        self.send_message(chat_id, f"{prefix}: {item.title}")
        self._remember_turn(chat_id, f"Korisnik je tražio unos: {item.raw or item.title}")
        self._remember_turn(chat_id, f"Ljilja je upisala {item.type}: {item.title}")

    @staticmethod
    def _negative_link_answer(text: str) -> bool:
        normalized = TelegramBot._strip_address(text).lower().strip()
        return normalized in {"ne", "nije", "nema veze", "bez veze", "samostalno", "preskoci", "preskoči"}

    @staticmethod
    def _project_from_link_answer(text: str) -> str:
        cleaned = TelegramBot._strip_address(text)
        cleaned = re.sub(r"^(da|jeste|vezano je za|za|projekat)\s+", "", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip(" .,-")

    def handle_pending_link_answer(self, chat_id: str, text: str) -> bool:
        item = self.pending_link_items.pop(str(chat_id), None)
        if item is None:
            return False
        if self._negative_link_answer(text):
            self.handle_item(chat_id, item, allow_link=False)
            return True
        project = self._project_from_link_answer(text)
        if not project:
            self.pending_link_items[str(chat_id)] = item
            self.send_message(chat_id, "Napiši naziv projekta/teme, ili reci 'ne' ako nema veze.")
            return True
        linked = ParsedItem(
            title=item.title,
            type=item.type,
            date=item.date,
            project=project,
            repeat=item.repeat,
            next_check=item.next_check,
            check_mode=item.check_mode,
            location=item.location,
            priority=item.priority,
            source=item.source,
            result=f"Povezano na osnovu korisnikovog odgovora: {project}",
            score=item.score,
            raw=item.raw,
        )
        self.handle_item(chat_id, linked, allow_link=False)
        return True

    def handle_correction(self, chat_id: str, corrected_text: str) -> None:
        if not corrected_text:
            self.send_message(chat_id, "Šta tačno da ispravim u poslednjem unosu?")
            return
        last = self.last_created_pages.get(str(chat_id))
        if not last:
            self.send_message(chat_id, "Nemam zapamćen poslednji unos za ispravku. Pošalji ceo ispravan tekst.")
            return
        page_id, _ = last
        item = parse_serbian(corrected_text, self.config.timezone)
        if self.config.dry_run:
            self.send_message(chat_id, f"DRY_RUN=true, ispravio bih poslednji unos na: {item.title}")
            self.last_created_pages[str(chat_id)] = (page_id, item.title)
            return
        if not self.config.notion_enabled:
            self.send_message(chat_id, "Notion nije podešen. Ne mogu da ispravim postojeći unos.")
            return
        self.notion.update_item(page_id, item)
        self.last_created_pages[str(chat_id)] = (page_id, item.title)
        self.send_message(chat_id, f"Ispravljeno u Notionu: {item.title}")

    def handle_free_text(self, chat_id: str, text: str) -> None:
        if self.handle_pending_link_answer(chat_id, text):
            return
        correction = self._correction_text(text)
        if correction is not None:
            self.handle_correction(chat_id, correction)
            return
        try:
            intent = self.fast_intent(text)
            if intent is None:
                self.send_message(chat_id, "Razmišljam...")
                intent = self.classify_intent(text, self.assistant_context(chat_id))
        except Exception as exc:
            if self.is_notion_entry(text):
                self.handle_item(chat_id, parse_serbian(self._strip_address(text), self.config.timezone))
                return
            self.send_message(chat_id, f"AI trenutno ne odgovara, ali Ljilja i dalje radi za podsetnike i Notion.\n{exc}")
            return

        action = intent.get("action")
        if action == "create_item":
            self.handle_item(chat_id, self._item_from_intent(text, intent))
            return
        if action == "clarify":
            reply = str(intent.get("reply") or "Na šta tačno da te podsetim?")
            self.send_message(chat_id, reply)
            self._remember_turn(chat_id, f"Korisnik: {text}")
            self._remember_turn(chat_id, f"Ljilja: {reply}")
            return
        reply = str(intent.get("reply") or "").strip()
        if reply:
            self.send_message(chat_id, reply)
            self._remember_turn(chat_id, f"Korisnik: {text}")
            self._remember_turn(chat_id, f"Ljilja: {reply}")
            return
        self.handle_chat(chat_id, text)

    def handle_text(self, chat_id: str, text: str) -> None:
        if text.startswith("/start"):
            self.send_message(chat_id, "Ljilja je spremna. Pošalji zadatak, belešku, plan ili /help.")
            return
        if text.startswith("/help"):
            self.send_message(
                chat_id,
                "/status, /mac, /danas, /sutra, /izvestaj, /jira naslov tiketa\n"
                "Primeri: Ljiljo jesi tu; zadatak: proveri ovo; sutra u 09 proveri Gmail filtere; "
                "beleška: danas sam rešio Telegram unos",
            )
            return
        if text.startswith("/status"):
            mode = "DRY_RUN" if self.config.dry_run else "LIVE"
            self.send_message(chat_id, f"Ljilja radi. Mode: {mode}. Notion: {'OK' if self.config.notion_enabled else 'nije podešen'}.")
            return
        if text.startswith("/mac"):
            self.send_message(chat_id, get_mac_status())
            return
        if text.startswith("/danas"):
            self.send_message(chat_id, self.day_summary(0))
            return
        if text.startswith("/sutra"):
            self.send_message(chat_id, self.day_summary(1))
            return
        if text.startswith("/izvestaj"):
            self.send_message(chat_id, self.report_text())
            return
        if text.startswith("/jira"):
            self.handle_jira(chat_id, text.removeprefix("/jira").strip())
            return
        self.handle_free_text(chat_id, text)

    def handle_jira(self, chat_id: str, title: str) -> None:
        if not title:
            self.send_message(chat_id, "Upotreba: /jira naslov tiketa")
            return
        if self.config.dry_run:
            self.send_message(chat_id, f"DRY_RUN=true, ne kreiram Jira issue.\nKreirao bih: {title}")
            return
        if not self.config.jira_enabled:
            self.send_message(chat_id, "Jira nije podešena. Popuni JIRA_* varijable.")
            return
        issue = self.jira.create_issue(title)
        self.send_message(chat_id, f"Jira issue kreiran: {issue.get('key', issue.get('id', 'OK'))}")

    def handle_callback(self, callback: dict[str, Any]) -> None:
        data = callback.get("data", "")
        callback_id = callback.get("id")
        message = callback.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        if not self._allowed(chat_id):
            return
        action, _, page_id = data.partition(":")
        status = ACTION_TO_STATUS.get(action)
        if not page_id:
            return
        if self.config.dry_run:
            text = f"DRY_RUN=true, obradio bih akciju {action} za {page_id}."
        else:
            tz = ZoneInfo(self.config.timezone)
            now = datetime.now(tz)
            if action == "snooze10":
                when = now + timedelta(minutes=10)
                self.notion.move_to_date(page_id, when, "Planned")
                self.notion.reschedule_check(page_id, when, "Planned")
                text = f"Podsetiću te kasnije: {when.strftime('%H:%M')}"
            elif action == "tomorrow":
                tomorrow = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
                self.notion.move_to_date(page_id, tomorrow, status)
                self.notion.reschedule_check(page_id, tomorrow, "Planned")
                text = f"Prebačeno za sutra: {tomorrow.strftime('%d.%m.%Y %H:%M')}"
            elif action == "skip_today":
                tomorrow = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
                self.notion.reschedule_check(page_id, tomorrow, "Planned")
                text = "Preskočeno za danas."
            else:
                if not status:
                    return
                self.notion.update_status(page_id, status)
                text = f"Status ažuriran: {status}"
        if callback_id:
            self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
        self.send_message(chat_id, text)

    def day_summary(self, days_ahead: int) -> str:
        tz = ZoneInfo(self.config.timezone)
        target = datetime.now(tz).date() + timedelta(days=days_ahead)
        start = datetime.combine(target, time.min, tzinfo=tz)
        end = start + timedelta(days=1)
        label = "Danas" if days_ahead == 0 else "Sutra"
        if self.config.dry_run:
            return f"{label}: DRY_RUN=true, ne čitam Notion. U live modu bih prikazao plan iz baze."
        pages = self.notion.query_day(start, end)
        if not pages:
            return f"{label}: nema stavki."
        return f"{label}:\n" + "\n".join(summarize_page(page) for page in pages)

    def report_text(self) -> str:
        if self.config.dry_run:
            return "Dnevni izveštaj: DRY_RUN=true, ne čitam Notion. U live modu bih sabrala današnje statuse."
        tz = ZoneInfo(self.config.timezone)
        today = datetime.now(tz).date()
        start = datetime.combine(today, time.min, tzinfo=tz)
        end = start + timedelta(days=1)
        pages = self.notion.query_day(start, end)
        if not pages:
            return "Dnevni izveštaj:\nDanas nema stavki u Notionu."
        counts: dict[str, int] = {}
        for page in pages:
            status = page_status(page)
            counts[status] = counts.get(status, 0) + 1
        count_line = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items()))
        return "Dnevni izveštaj:\n" + count_line + "\n\n" + "\n".join(summarize_page(page) for page in pages)

    def run(self) -> None:
        while True:
            try:
                self.send_daily_report_if_due()
                self.send_due_reminders()
                self.send_due_checks()
                updates = self._api("getUpdates", {"timeout": 20, "offset": self.offset}).get("result", [])
                for update in updates:
                    self.offset = max(self.offset, update["update_id"] + 1)
                    if "callback_query" in update:
                        self.handle_callback(update["callback_query"])
                        continue
                    message = update.get("message", {})
                    chat = message.get("chat", {})
                    chat_id = str(chat.get("id", ""))
                    if not self._allowed(chat_id):
                        continue
                    text = message.get("text", "")
                    if text:
                        self.handle_text(chat_id, text)
            except KeyboardInterrupt:
                raise
            except Exception:
                traceback.print_exc()
                sleep_time.sleep(5)
