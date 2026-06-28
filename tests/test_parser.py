from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

from ljilja_assistant.parser import parse_serbian


class ParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 6, 25, 12, 0, tzinfo=ZoneInfo("Europe/Belgrade"))

    def test_tomorrow_task(self) -> None:
        item = parse_serbian("sutra u 09 proveri Gmail filtere", now=self.now)
        self.assertEqual(item.type, "Task")
        self.assertEqual(item.title, "proveri Gmail filtere")
        self.assertEqual(item.date.isoformat(), "2026-06-26T09:00:00+02:00")
        self.assertEqual(item.project, "Gmail")

    def test_explicit_date(self) -> None:
        item = parse_serbian("15.07.2026 u 20h backup Mac-a", now=self.now)
        self.assertEqual(item.title, "backup Mac-a")
        self.assertEqual(item.date.isoformat(), "2026-07-15T20:00:00+02:00")

    def test_weekday(self) -> None:
        item = parse_serbian("u petak 18:30 sastanak ellco.pro", now=self.now)
        self.assertEqual(item.title, "sastanak ellco.pro")
        self.assertEqual(item.date.isoformat(), "2026-06-26T18:30:00+02:00")
        self.assertEqual(item.project, "ellco.pro")

    def test_note(self) -> None:
        item = parse_serbian("beleška: danas sam rešio Telegram unos", now=self.now)
        self.assertEqual(item.type, "Note")
        self.assertEqual(item.title, "sam rešio Telegram unos")

    def test_plan(self) -> None:
        item = parse_serbian("plan za sutra: 1h JuristSoft, 30min ellco.pro", now=self.now)
        self.assertEqual(item.type, "Plan")
        self.assertEqual(item.date.isoformat(), "2026-06-26T09:00:00+02:00")
        self.assertEqual(item.project, "JuristSoft")


if __name__ == "__main__":
    unittest.main()
