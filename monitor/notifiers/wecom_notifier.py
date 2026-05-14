from __future__ import annotations

from .exceptions import BusinessError
from .http_utils import http_json_post


class WecomNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, subject: str, body: str) -> None:
        payload = {"msgtype": "text", "text": {"content": f"{subject}\n\n{body}"}}
        resp = http_json_post(self.webhook_url, payload)
        if isinstance(resp, dict) and resp.get("errcode", 0) != 0:
            raise BusinessError(f"WeCom business error: {resp}")

