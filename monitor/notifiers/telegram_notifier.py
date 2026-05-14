from __future__ import annotations

from .http_utils import http_json_post


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, api_base: str = "https://api.telegram.org") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = api_base.rstrip("/")

    def send(self, subject: str, body: str) -> None:
        text = f"{subject}\n\n{body}"
        url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}
        http_json_post(url, payload)
