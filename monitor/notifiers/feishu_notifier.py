from __future__ import annotations

from .exceptions import BusinessError
from .http_utils import http_json_post


class FeishuNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, subject: str, body: str) -> None:
        payload = {"msg_type": "text", "content": {"text": f"{subject}\n\n{body}"}}
        resp = http_json_post(self.webhook_url, payload)
        if isinstance(resp, dict):
            code = resp.get("code", 0)
            if code not in (0, None):
                raise BusinessError(f"Feishu business error: {resp}")

