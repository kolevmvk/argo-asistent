from __future__ import annotations

import unittest

from ljilja_assistant.config import Config


class ConfigTest(unittest.TestCase):
    def test_requires_telegram(self) -> None:
        config = Config.load(env_file="/tmp/ljilja-assistant-missing.env", environ={})
        errors = config.validate()
        self.assertIn("TELEGRAM_BOT_TOKEN is required", errors)
        self.assertIn("TELEGRAM_ALLOWED_CHAT_ID is required", errors)

    def test_live_requires_notion(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DRY_RUN": "false",
            }
        )
        self.assertIn("NOTION_TOKEN and NOTION_DATABASE_ID are required when DRY_RUN=false", config.validate())

    def test_jira_enabled_only_when_complete(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "JIRA_BASE_URL": "https://example.atlassian.net",
                "JIRA_EMAIL": "me@example.com",
                "JIRA_API_TOKEN": "token",
                "JIRA_PROJECT_KEY": "OPS",
                "JIRA_ISSUE_TYPE": "Task",
            }
        )
        self.assertTrue(config.jira_enabled)

    def test_default_ollama_model_matches_local_setup(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALLOWED_CHAT_ID": "1"},
        )
        self.assertEqual(config.ollama_model, "gemma3:4b")

    def test_daily_report_time_validation(self) -> None:
        config = Config.load(
            env_file="/tmp/ljilja-assistant-missing.env",
            environ={
                "TELEGRAM_BOT_TOKEN": "x",
                "TELEGRAM_ALLOWED_CHAT_ID": "1",
                "DAILY_REPORT_TIME": "29:99",
            },
        )
        self.assertIn("DAILY_REPORT_TIME must use HH:MM format", config.validate())


if __name__ == "__main__":
    unittest.main()
