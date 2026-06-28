from __future__ import annotations

from typing import Any
import base64
import json
import urllib.error
import urllib.request

from .config import Config


class JiraClient:
    def __init__(self, config: Config):
        self.config = config

    def create_issue(self, title: str, description: str = "Created from Ljilja Telegram command.") -> dict[str, Any]:
        auth = base64.b64encode(f"{self.config.jira_email}:{self.config.jira_api_token}".encode("utf-8")).decode("ascii")
        payload = {
            "fields": {
                "project": {"key": self.config.jira_project_key},
                "summary": title,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}],
                        }
                    ],
                },
                "issuetype": {"name": self.config.jira_issue_type},
            }
        }
        request = urllib.request.Request(
            f"{self.config.jira_base_url.rstrip('/')}/rest/api/3/issue",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Basic {auth}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Jira API error {exc.code}: {detail}") from exc
