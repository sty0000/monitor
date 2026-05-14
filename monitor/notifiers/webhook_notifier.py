from __future__ import annotations

from .http_utils import http_json_post


class WebhookNotifier:
    def __init__(self, url: str, timeout_seconds: int = 10) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def send(self, subject: str, body: str) -> None:
        payload = {"subject": subject, "text": body}
        http_json_post(self.url, payload, timeout=self.timeout_seconds)

