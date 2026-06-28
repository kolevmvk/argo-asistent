from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import json
import unittest
from unittest.mock import patch

from ljilja_assistant.config import Config
from ljilja_assistant.ollama_client import OllamaClient
from ljilja_assistant.parser import parse_serbian
from ljilja_assistant.telegram_bot import TelegramBot


class DryRunBot(TelegramBot):
    def __init__(self, config: Config):
        super().__init__(config)
        self.sent: list[str] = []
        self.notion_called = False
        self.jira_called = False
        self.intents: dict[str, dict] = {}
        self.classify_calls = 0
        self.sent_pages: list[str | None] = []

    def send_message(self, chat_id: str, text: str, page_id: str | None = None) -> None:
        self.sent.append(text)
        self.sent_pages.append(page_id)

    def handle_chat(self, chat_id: str, text: str) -> None:
        self.sent.append("AI odgovor")

    def classify_intent(self, text: str, context: str = "") -> dict:
        self.classify_calls += 1
        return self.intents.get(text, {"action": "chat", "reply": "AI odgovor"})


class TimeoutIntentBot(DryRunBot):
    def classify_intent(self, text: str, context: str = "") -> dict:
        raise TimeoutError("timed out")


class ChatBotForTimeout(TelegramBot):
    def __init__(self, config: Config):
        super().__init__(config)
        self.sent: list[str] = []

    def send_message(self, chat_id: str, text: str, page_id: str | None = None) -> None:
        self.sent.append(text)


class BrokenOllama:
    def ask(self, text: str, context: str = "") -> str:
        raise TimeoutError("timed out")


class ContextOllama:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.link_decision = {"action": "none"}

    def ask(self, text: str, context: str = "") -> str:
        self.calls.append((text, context))
        return "AI odgovor sa memorijom"

    def link_memory(self, item: dict, context: str) -> dict:
        return self.link_decision


class FakeOllamaResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"message": {"content": "{\"action\":\"chat\",\"reply\":\"OK\"}"}}).encode("utf-8")


class FakeNotion:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []
        self.rescheduled: list[tuple[str, str]] = []
        self.items_updated: list[tuple[str, str]] = []
        self.created_items: list[object] = []

    def query_due(self, now: datetime) -> list[dict]:
        return [
            {
                "id": "page-1",
                "properties": {
                    "Title": {"title": [{"plain_text": "idem da piskim"}]},
                    "Status": {"select": {"name": "Planned"}},
                    "Date": {"date": {"start": now.isoformat()}},
                },
            }
        ]

    def query_assistant_context(self, page_size: int = 12) -> list[dict]:
        return [
            {
                "id": "memory-1",
                "properties": {
                    "Title": {"title": [{"plain_text": "korisnik voli kratke odgovore"}]},
                    "Type": {"select": {"name": "Note"}},
                    "Status": {"select": {"name": "Planned"}},
                },
            }
        ]

    def update_status(self, page_id: str, status: str) -> None:
        self.updated.append((page_id, status))

    def query_due_checks(self, now: datetime) -> list[dict]:
        return [
            {
                "id": "page-2",
                "properties": {
                    "Title": {"title": [{"plain_text": "popijem lek"}]},
                    "Status": {"select": {"name": "Planned"}},
                    "Repeat": {"select": {"name": "Daily"}},
                    "CheckMode": {"select": {"name": "AskUntilDone"}},
                    "NextCheck": {"date": {"start": now.isoformat()}},
                },
            }
        ]

    def reschedule_check(self, page_id: str, next_check: datetime, status: str = "Planned") -> None:
        self.rescheduled.append((page_id, status))

    def create_item(self, item) -> dict:
        self.created_items.append(item)
        return {"id": "created-page"}

    def update_item(self, page_id: str, item) -> dict:
        self.items_updated.append((page_id, item.title))
        return {"id": page_id}


class DryRunTest(unittest.TestCase):
    def test_ollama_output_strips_thinking_process(self) -> None:
        text = "Thinking Process:\n1. Analyze.\n\nOdgovor: Ja sam Ljilja."
        self.assertEqual(OllamaClient._clean_output(text), "Ja sam Ljilja.")

    def test_ollama_output_strips_think_tags(self) -> None:
        text = "<think>interno razmisljanje</think>\nJa sam Ljilja."
        self.assertEqual(OllamaClient._clean_output(text), "Ja sam Ljilja.")

    def test_ollama_uses_gemma3_fast_json_profile(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1"},
        )
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeOllamaResponse()

        with patch("urllib.request.urlopen", fake_urlopen):
            OllamaClient(config).extract_intent("ko si ti?", "2026-06-28T12:00:00+02:00", "Europe/Belgrade")

        payload = captured["payload"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["model"], "gemma3:4b")
        self.assertEqual(payload["keep_alive"], "1h")
        self.assertEqual(payload["format"], "json")
        self.assertEqual(captured["timeout"], 6)
        self.assertEqual(payload["options"]["temperature"], 0.0)
        self.assertEqual(payload["options"]["num_predict"], 96)

    def test_dry_run_does_not_create_notion_item(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.intents["sutra u 09 proveri Gmail filtere"] = {
            "action": "create_item",
            "type": "Task",
            "title": "proveri Gmail filtere",
            "date_iso": "2026-06-28T09:00:00+02:00",
            "project": "Gmail",
        }
        bot.handle_text("1", "sutra u 09 proveri Gmail filtere")
        self.assertIn("DRY_RUN=true", bot.sent[-1])
        self.assertIn("Upisao bih", bot.sent[-1])

    def test_dry_run_does_not_create_jira_issue(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.handle_text("1", "/jira Test tiket")
        self.assertIn("ne kreiram Jira issue", bot.sent[-1])

    def test_question_goes_to_ai_even_in_dry_run(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.handle_text("1", "ko si ti")
        self.assertEqual(bot.sent[-1], "AI odgovor")

    def test_addressed_question_goes_to_ai(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.handle_text("1", "Ljiljo ko si ti?")
        self.assertEqual(bot.sent[-1], "AI odgovor")

    def test_plain_sentence_without_date_goes_to_ai(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.handle_text("1", "proveri ovo")
        self.assertEqual(bot.sent[-1], "AI odgovor")

    def test_ai_timeout_does_not_crash_free_text(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TimeoutIntentBot(config)
        bot.handle_text("1", "objasni mi nesto")
        self.assertIn("AI trenutno ne odgovara", bot.sent[-1])

    def test_ai_timeout_does_not_crash_chat(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = ChatBotForTimeout(config)
        bot.ollama = BrokenOllama()  # type: ignore[assignment]
        bot.handle_chat("1", "ko si ti")
        self.assertEqual(bot.sent[-2], "Razmišljam...")
        self.assertIn("Ne mogu trenutno", bot.sent[-1])

    def test_explicit_task_prefix_still_goes_to_notion_flow(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.intents["zadatak: proveri ovo"] = {
            "action": "create_item",
            "type": "Task",
            "title": "proveri ovo",
            "date_iso": None,
            "project": "",
        }
        bot.handle_text("1", "zadatak: proveri ovo")
        self.assertIn("DRY_RUN=true", bot.sent[-1])
        self.assertIn("Upisao bih: proveri ovo", bot.sent[-1])

    def test_correction_updates_last_item_instead_of_creating_new_one(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = DryRunBot(config)
        fake = FakeNotion()
        bot.notion = fake  # type: ignore[assignment]
        bot.handle_item("1", parse_serbian("sutra u 9 pogresan tekst", config.timezone))

        bot.handle_text("1", "ispravi u sutra u 9 ispravan tekst")

        self.assertEqual(fake.items_updated, [("created-page", "ispravan tekst")])
        self.assertEqual(bot.sent[-1], "Ispravljeno u Notionu: ispravan tekst")

    def test_correction_does_not_go_to_ai_or_new_notion_item(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.handle_item("1", parse_serbian("sutra u 9 pogresan tekst", config.timezone))

        bot.handle_text("1", "nije pogresan tekst nego ispravan tekst")

        self.assertEqual(bot.classify_calls, 0)
        self.assertIn("ispravio bih poslednji unos na: ispravan tekst", bot.sent[-1])

    def test_empty_correction_asks_clarification(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.handle_text("1", "ispravi")
        self.assertEqual(bot.sent[-1], "Šta tačno da ispravim u poslednjem unosu?")

    def test_reminder_request_goes_to_notion_flow(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.intents["podseti me sutra da pozovem Marka"] = {
            "action": "create_item",
            "type": "Task",
            "title": "pozovi Marka",
            "date_iso": "2026-06-28T09:00:00+02:00",
            "project": "",
        }
        bot.handle_text("1", "podseti me sutra da pozovem Marka")
        self.assertIn("DRY_RUN=true", bot.sent[-1])
        self.assertIn("Upisao bih: pozovem Marka", bot.sent[-1])

    def test_memory_request_goes_to_notion_note(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.handle_text("1", "Ljiljo zapamti da volim kratke odgovore")
        self.assertIn("Type: Note", bot.sent[-1])
        self.assertIn("Upisao bih: volim kratke odgovore", bot.sent[-1])

    def test_live_memory_confirmation_says_remembered(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = DryRunBot(config)
        bot.notion = FakeNotion()  # type: ignore[assignment]
        bot.handle_text("1", "Ljiljo zapamti da volim kratke odgovore")
        self.assertEqual(bot.sent[-1], "Zapamćeno u Notionu: volim kratke odgovore")

    def test_memory_linking_enriches_item_before_create(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = DryRunBot(config)
        fake_notion = FakeNotion()
        fake_ollama = ContextOllama()
        fake_ollama.link_decision = {
            "action": "link",
            "project": "ellco.pro",
            "relation_note": "Povezano sa postojećim Ellco kontekstom.",
        }
        bot.notion = fake_notion  # type: ignore[assignment]
        bot.ollama = fake_ollama  # type: ignore[assignment]

        bot.handle_text("1", "Ljiljo zapamti da Milan duguje odgovor")

        created = fake_notion.created_items[-1]
        self.assertEqual(created.project, "ellco.pro")
        self.assertEqual(created.result, "Povezano sa postojećim Ellco kontekstom.")
        self.assertEqual(bot.sent[-1], "Zapamćeno u Notionu: Milan duguje odgovor")

    def test_memory_linking_asks_when_relation_is_ambiguous(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = DryRunBot(config)
        fake_notion = FakeNotion()
        fake_ollama = ContextOllama()
        fake_ollama.link_decision = {
            "action": "ask",
            "question": "Da li je ovo vezano za ellco.pro ili za drugi projekat?",
        }
        bot.notion = fake_notion  # type: ignore[assignment]
        bot.ollama = fake_ollama  # type: ignore[assignment]

        bot.handle_text("1", "Ljiljo zapamti da Milan duguje odgovor")

        self.assertEqual(fake_notion.created_items, [])
        self.assertEqual(bot.sent[-1], "Da li je ovo vezano za ellco.pro ili za drugi projekat?")

    def test_chat_uses_notion_memory_context(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = ChatBotForTimeout(config)
        fake_notion = FakeNotion()
        fake_ollama = ContextOllama()
        bot.notion = fake_notion  # type: ignore[assignment]
        bot.ollama = fake_ollama  # type: ignore[assignment]
        bot.handle_chat("1", "šta znaš o meni?")
        self.assertIn("korisnik voli kratke odgovore", fake_ollama.calls[-1][1])
        self.assertEqual(bot.sent[-1], "AI odgovor sa memorijom")

    def test_chat_uses_recent_conversation_context(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = ChatBotForTimeout(config)
        fake_ollama = ContextOllama()
        bot.ollama = fake_ollama  # type: ignore[assignment]
        bot._remember_turn("1", "Korisnik: zovi projekat Argo")
        bot.handle_chat("1", "kako se zove projekat?")
        self.assertIn("zovi projekat Argo", fake_ollama.calls[-1][1])
        self.assertEqual(bot.sent[-1], "AI odgovor sa memorijom")

    def test_vague_reminder_asks_for_clarification(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = DryRunBot(config)
        bot.intents["Ljiljo podsetime stra"] = {
            "action": "clarify",
            "reply": "Na šta tačno da te podsetim sutra?",
        }
        bot.handle_text("1", "Ljiljo podsetime stra")
        self.assertEqual(bot.sent[-1], "Na šta tačno da te podsetim sutra?")

    def test_daily_report_due_once_per_day(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "true",
                "DAILY_REPORT_TIME": "21:30",
            },
        )
        bot = DryRunBot(config)
        now = datetime(2026, 6, 27, 21, 31, tzinfo=ZoneInfo("Europe/Belgrade"))
        self.assertTrue(bot._daily_report_due(now))
        bot.last_daily_report_date = "2026-06-27"
        self.assertFalse(bot._daily_report_due(now))

    def test_fast_presence_intent_does_not_need_model(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo jesi tu")
        self.assertEqual(intent, {"action": "chat", "reply": "Tu sam. Reci šta treba."})

    def test_fast_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podseti me sutra da pozovem Marka")
        self.assertEqual(intent["action"], "create_item")
        self.assertEqual(intent["title"], "pozovem Marka")
        self.assertIn("T09:00:00", intent["date_iso"])

    def test_fast_vague_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podsetime stra")
        self.assertEqual(intent["action"], "clarify")

    def test_fast_relative_minute_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podseti me za 5 minuta da idem da piskim")
        self.assertEqual(intent["action"], "create_item")
        self.assertEqual(intent["title"], "idem da piskim")
        self.assertIsNotNone(intent["date_iso"])

    def test_fast_relative_minute_word_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podseti me za pet minuta da idem da piskim")
        self.assertEqual(intent["action"], "create_item")
        self.assertEqual(intent["title"], "idem da piskim")

    def test_due_reminder_sends_message_and_marks_in_progress(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = DryRunBot(config)
        fake = FakeNotion()
        bot.notion = fake  # type: ignore[assignment]
        bot.send_due_reminders()
        self.assertEqual(bot.sent[-1], "Podsetnik: idem da piskim")
        self.assertEqual(bot.sent_pages[-1], "page-1")
        self.assertEqual(fake.updated, [("page-1", "In Progress")])

    def test_initial_live_notion_confirmation_has_no_buttons(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = DryRunBot(config)
        bot.notion = FakeNotion()  # type: ignore[assignment]
        intent = bot.fast_intent("Ljiljo podseti me za pet minuta da integrisem i mejl")
        item = bot._item_from_intent("Ljiljo podseti me za pet minuta da integrisem i mejl", intent)
        bot.handle_item("1", item)
        self.assertEqual(bot.sent[-1], "Upisano u Notion: integrisem i mejl")
        self.assertIsNone(bot.sent_pages[-1])

    def test_due_daily_check_sends_question_and_reschedules(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
                "NOTION_TOKEN": "n",
                "NOTION_DATABASE_ID": "d",
            },
        )
        bot = DryRunBot(config)
        fake = FakeNotion()
        bot.notion = fake  # type: ignore[assignment]
        bot.send_due_checks()
        self.assertEqual(bot.sent[-1], "Da li je urađeno: popijem lek")
        self.assertEqual(fake.rescheduled, [("page-2", "Planned")])

    def test_fast_daily_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo proveravaj svaki dan u 21 da li sam uneo izveštaj")
        self.assertEqual(intent["action"], "create_item")
        self.assertEqual(intent["type"], "Reminder")
        self.assertEqual(intent["title"], "uneo izveštaj")
        self.assertEqual(intent["repeat"], "Daily")
        self.assertEqual(intent["check_mode"], "AskUntilDone")
        self.assertIsNotNone(intent["next_check_iso"])

    def test_fast_weekday_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podsećaj me radnim danima u 8 da proverim Jira")
        self.assertEqual(intent["repeat"], "Weekdays")
        self.assertEqual(intent["title"], "proverim Jira")

    def test_fast_weekly_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podsećaj me svakog ponedeljka u 8 da proverim Jira")
        self.assertEqual(intent["repeat"], "Weekly")
        self.assertEqual(intent["title"], "proverim Jira")

    def test_fast_monthly_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podsećaj me svakog 15. u mesecu u 10 da platim hosting")
        self.assertEqual(intent["repeat"], "Monthly")
        self.assertEqual(intent["title"], "platim hosting")

    def test_fast_yearly_reminder_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo podsećaj me svakog 15.07 u 10 da proverim ugovor")
        self.assertEqual(intent["repeat"], "Yearly")
        self.assertEqual(intent["title"], "proverim ugovor")

    def test_fast_deadline_task_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo treba da završim ponudu do petka")
        self.assertEqual(intent["action"], "create_item")
        self.assertEqual(intent["type"], "Task")
        self.assertEqual(intent["title"], "završim ponudu")
        self.assertIsNotNone(intent["date_iso"])

    def test_fast_duty_intent(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1", "DRY_RUN": "true"},
        )
        bot = TelegramBot(config)
        intent = bot.fast_intent("Ljiljo dežuran sam 15.07.2026 od 20h u JuristSoft")
        self.assertEqual(intent["action"], "create_item")
        self.assertEqual(intent["type"], "Duty")
        self.assertEqual(intent["project"], "JuristSoft")
        self.assertEqual(intent["location"], "JuristSoft")


if __name__ == "__main__":
    unittest.main()
