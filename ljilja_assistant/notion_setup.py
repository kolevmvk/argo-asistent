from __future__ import annotations

from typing import Any
import json
import sys
import urllib.error
import urllib.request

from .config import Config


def _api(config: Config, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.notion.com/v1{path}",
        data=json.dumps(payload).encode("utf-8"),
        method=method,
        headers={
            "Authorization": f"Bearer {config.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {exc.code}: {detail}") from exc


def _request(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    return _api(config, "POST", "/databases", payload)


REPEAT_PROPERTY = {
    "select": {
        "options": [
            {"name": "None", "color": "gray"},
            {"name": "Daily", "color": "blue"},
            {"name": "Weekdays", "color": "green"},
            {"name": "Weekly", "color": "purple"},
            {"name": "Monthly", "color": "yellow"},
            {"name": "Yearly", "color": "red"},
        ]
    }
}


def create_database(config: Config, parent_page_id: str, title: str = "Ljilja") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": {
            "Title": {"title": {}},
            "Type": {
                "select": {
                    "options": [
                        {"name": "Task", "color": "blue"},
                        {"name": "Note", "color": "gray"},
                        {"name": "Plan", "color": "green"},
                        {"name": "Reminder", "color": "yellow"},
                        {"name": "Event", "color": "purple"},
                        {"name": "Duty", "color": "red"},
                    ]
                }
            },
            "Project": {"rich_text": {}},
            "Location": {"rich_text": {}},
            "Date": {"date": {}},
            "NextCheck": {"date": {}},
            "LastAsked": {"date": {}},
            "Repeat": REPEAT_PROPERTY,
            "CheckMode": {
                "select": {
                    "options": [
                        {"name": "Reminder", "color": "blue"},
                        {"name": "AskUntilDone", "color": "orange"},
                    ]
                }
            },
            "Status": {
                "select": {
                    "options": [
                        {"name": "Planned", "color": "blue"},
                        {"name": "In Progress", "color": "yellow"},
                        {"name": "Done", "color": "green"},
                        {"name": "Partial", "color": "orange"},
                        {"name": "Skipped", "color": "gray"},
                        {"name": "Moved", "color": "purple"},
                        {"name": "Cancelled", "color": "red"},
                    ]
                }
            },
            "Priority": {
                "select": {
                    "options": [
                        {"name": "Low", "color": "gray"},
                        {"name": "Normal", "color": "blue"},
                        {"name": "High", "color": "red"},
                    ]
                }
            },
            "Source": {
                "select": {
                    "options": [
                        {"name": "Telegram", "color": "green"},
                    ]
                }
            },
            "Result": {"rich_text": {}},
            "Score": {"number": {"format": "number"}},
        },
    }
    return _request(config, payload)


def update_database(config: Config, database_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "properties": {
            "Repeat": REPEAT_PROPERTY,
        }
    }
    return _api(config, "PATCH", f"/databases/{database_id}", payload)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "Usage: python3 -m ljilja_assistant.notion_setup <PARENT_PAGE_ID> [DATABASE_TITLE]\n"
            "       python3 -m ljilja_assistant.notion_setup --update-current",
            file=sys.stderr,
        )
        return 2
    config = Config.load()
    if not config.notion_token:
        print("Config error: NOTION_TOKEN is required", file=sys.stderr)
        return 2
    if args[0] == "--update-current":
        if not config.notion_database_id:
            print("Config error: NOTION_DATABASE_ID is required", file=sys.stderr)
            return 2
        update_database(config, config.notion_database_id)
        print("Notion database schema updated.")
        return 0
    parent_page_id = args[0]
    title = args[1] if len(args) > 1 else "Ljilja"
    created = create_database(config, parent_page_id, title)
    database_id = created.get("id", "")
    url = created.get("url", "")
    print(f"NOTION_DATABASE_ID={database_id}")
    if url:
        print(f"URL={url}")
    print("Copy NOTION_DATABASE_ID into .env, then keep DRY_RUN=true for the first test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
