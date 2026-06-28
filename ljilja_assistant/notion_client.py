from __future__ import annotations

from datetime import datetime
from typing import Any
import json
import urllib.error
import urllib.request

from .config import Config
from .parser import ParsedItem


STATUSES = ["Planned", "In Progress", "Done", "Partial", "Skipped", "Moved", "Cancelled"]


class NotionClient:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = "https://api.notion.com/v1"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.notion_token}",
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

    @staticmethod
    def _properties(item: ParsedItem, status: str = "Planned") -> dict[str, Any]:
        props: dict[str, Any] = {
            "Title": {"title": [{"text": {"content": item.title}}]},
            "Type": {"select": {"name": item.type}},
            "Status": {"select": {"name": status}},
            "Priority": {"select": {"name": item.priority}},
            "Source": {"select": {"name": item.source}},
        }
        if item.project:
            props["Project"] = {"rich_text": [{"text": {"content": item.project}}]}
        if item.location:
            props["Location"] = {"rich_text": [{"text": {"content": item.location}}]}
        if item.repeat:
            props["Repeat"] = {"select": {"name": item.repeat}}
        if item.check_mode:
            props["CheckMode"] = {"select": {"name": item.check_mode}}
        if item.date:
            props["Date"] = {"date": {"start": item.date.isoformat()}}
        if item.next_check:
            props["NextCheck"] = {"date": {"start": item.next_check.isoformat()}}
        if item.result:
            props["Result"] = {"rich_text": [{"text": {"content": item.result}}]}
        if item.score is not None:
            props["Score"] = {"number": item.score}
        return props

    def create_item(self, item: ParsedItem) -> dict[str, Any]:
        payload = {
            "parent": {"database_id": self.config.notion_database_id},
            "properties": self._properties(item),
        }
        return self._request("POST", "/pages", payload)

    @staticmethod
    def _update_properties(item: ParsedItem) -> dict[str, Any]:
        props: dict[str, Any] = {
            "Title": {"title": [{"text": {"content": item.title}}]},
        }
        if item.type:
            props["Type"] = {"select": {"name": item.type}}
        if item.project:
            props["Project"] = {"rich_text": [{"text": {"content": item.project}}]}
        if item.location:
            props["Location"] = {"rich_text": [{"text": {"content": item.location}}]}
        if item.date:
            props["Date"] = {"date": {"start": item.date.isoformat()}}
        return props

    def update_item(self, page_id: str, item: ParsedItem) -> dict[str, Any]:
        return self.update_page(page_id, self._update_properties(item))

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def update_status(self, page_id: str, status: str) -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        return self.update_page(page_id, {"Status": {"select": {"name": status}}})

    def move_to_date(self, page_id: str, when: datetime, status: str = "Moved") -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        return self.update_page(
            page_id,
            {
                "Status": {"select": {"name": status}},
                "Date": {"date": {"start": when.isoformat()}},
            },
        )

    def query_day(self, day_start: datetime, day_end: datetime) -> list[dict[str, Any]]:
        payload = {
            "filter": {
                "and": [
                    {"property": "Date", "date": {"on_or_after": day_start.isoformat()}},
                    {"property": "Date", "date": {"before": day_end.isoformat()}},
                ]
            },
            "sorts": [{"property": "Date", "direction": "ascending"}],
        }
        return self._request("POST", f"/databases/{self.config.notion_database_id}/query", payload).get("results", [])

    def query_due(self, now: datetime) -> list[dict[str, Any]]:
        payload = {
            "filter": {
                "and": [
                    {"property": "Date", "date": {"on_or_before": now.isoformat()}},
                    {"property": "NextCheck", "date": {"is_empty": True}},
                    {"property": "Status", "select": {"equals": "Planned"}},
                ]
            },
            "sorts": [{"property": "Date", "direction": "ascending"}],
            "page_size": 20,
        }
        return self._request("POST", f"/databases/{self.config.notion_database_id}/query", payload).get("results", [])

    def query_due_checks(self, now: datetime) -> list[dict[str, Any]]:
        payload = {
            "filter": {
                "and": [
                    {"property": "NextCheck", "date": {"on_or_before": now.isoformat()}},
                    {"property": "Status", "select": {"does_not_equal": "Cancelled"}},
                    {"property": "Status", "select": {"does_not_equal": "Done"}},
                ]
            },
            "sorts": [{"property": "NextCheck", "direction": "ascending"}],
            "page_size": 20,
        }
        return self._request("POST", f"/databases/{self.config.notion_database_id}/query", payload).get("results", [])

    def reschedule_check(self, page_id: str, next_check: datetime, status: str = "Planned") -> dict[str, Any]:
        return self.update_page(
            page_id,
            {
                "NextCheck": {"date": {"start": next_check.isoformat()}},
                "LastAsked": {"date": {"start": datetime.now(next_check.tzinfo).isoformat()}},
                "Status": {"select": {"name": status}},
            },
        )


def summarize_page(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    title_items = props.get("Title", {}).get("title", [])
    title = title_items[0]["plain_text"] if title_items else "(bez naslova)"
    status = props.get("Status", {}).get("select", {}) or {}
    date_prop = props.get("Date", {}).get("date", {}) or {}
    when = date_prop.get("start", "")
    return f"- {title} | {status.get('name', 'bez statusa')} | {when}"


def page_title(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    title_items = props.get("Title", {}).get("title", [])
    return title_items[0]["plain_text"] if title_items else "(bez naslova)"


def page_status(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    status = props.get("Status", {}).get("select", {}) or {}
    return status.get("name", "bez statusa")


def page_repeat(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    repeat = props.get("Repeat", {}).get("select", {}) or {}
    return repeat.get("name", "None")


def page_check_mode(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    mode = props.get("CheckMode", {}).get("select", {}) or {}
    return mode.get("name", "Reminder")


def page_next_check(page: dict[str, Any]) -> datetime | None:
    props = page.get("properties", {})
    next_check = props.get("NextCheck", {}).get("date", {}) or {}
    start = next_check.get("start")
    if not start:
        return None
    return datetime.fromisoformat(start)
