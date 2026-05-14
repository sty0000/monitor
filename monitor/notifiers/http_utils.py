from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .exceptions import HttpError, TransportError


def http_json_post(url: str, payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", errors="ignore")
            if code < 200 or code >= 300:
                raise HttpError(f"HTTP status {code}: {body}")
            if not body.strip():
                return {}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"raw": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
        raise HttpError(f"HTTP status {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise TransportError(f"Transport error: {exc}") from exc

