from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse

from .exceptions import BusinessError
from .http_utils import http_json_post


class DingtalkNotifier:
    def __init__(self, webhook_url: str, secret: str = "") -> None:
        self.webhook_url = webhook_url
        self.secret = secret

    def _build_url(self) -> str:
        if not self.secret:
            return self.webhook_url
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        digest = hmac.new(self.secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        sep = "&" if "?" in self.webhook_url else "?"
        return f"{self.webhook_url}{sep}timestamp={timestamp}&sign={sign}"

    def send(self, subject: str, body: str) -> None:
        payload = {"msgtype": "text", "text": {"content": f"{subject}\n\n{body}"}}
        resp = http_json_post(self._build_url(), payload)
        if isinstance(resp, dict) and resp.get("errcode", 0) != 0:
            raise BusinessError(f"DingTalk business error: {resp}")

