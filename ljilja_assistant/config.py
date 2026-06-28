from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import os
import re


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_allowed_chat_id: str
    notion_token: str
    notion_database_id: str
    ollama_url: str
    ollama_model: str
    timezone: str
    dry_run: bool
    daily_report_time: str
    jira_base_url: str
    jira_email: str
    jira_api_token: str
    jira_project_key: str
    jira_issue_type: str

    @property
    def jira_enabled(self) -> bool:
        return all(
            [
                self.jira_base_url,
                self.jira_email,
                self.jira_api_token,
                self.jira_project_key,
                self.jira_issue_type,
            ]
        )

    @property
    def notion_enabled(self) -> bool:
        return bool(self.notion_token and self.notion_database_id)

    @classmethod
    def load(
        cls,
        env_file: str | Path = ".env",
        environ: Mapping[str, str] | None = None,
    ) -> "Config":
        file_values = _read_env_file(Path(env_file))
        source = dict(os.environ if environ is None else environ)
        merged = {**file_values, **source}
        return cls(
            telegram_bot_token=merged.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_chat_id=merged.get("TELEGRAM_ALLOWED_CHAT_ID", ""),
            notion_token=merged.get("NOTION_TOKEN", ""),
            notion_database_id=merged.get("NOTION_DATABASE_ID", ""),
            ollama_url=merged.get("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=merged.get("OLLAMA_MODEL", "gemma3:4b"),
            timezone=merged.get("TIMEZONE", "Europe/Belgrade"),
            dry_run=_bool(merged.get("DRY_RUN"), default=True),
            daily_report_time=merged.get("DAILY_REPORT_TIME", "21:30"),
            jira_base_url=merged.get("JIRA_BASE_URL", ""),
            jira_email=merged.get("JIRA_EMAIL", ""),
            jira_api_token=merged.get("JIRA_API_TOKEN", ""),
            jira_project_key=merged.get("JIRA_PROJECT_KEY", ""),
            jira_issue_type=merged.get("JIRA_ISSUE_TYPE", "Task"),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        if not self.telegram_allowed_chat_id:
            errors.append("TELEGRAM_ALLOWED_CHAT_ID is required")
        if not self.dry_run and not self.notion_enabled:
            errors.append("NOTION_TOKEN and NOTION_DATABASE_ID are required when DRY_RUN=false")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", self.daily_report_time):
            errors.append("DAILY_REPORT_TIME must use HH:MM format")
        return errors
