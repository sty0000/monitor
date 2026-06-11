from __future__ import annotations

import json
import logging

from monitor.config import LoggingConfig
from monitor.logging_utils import JsonFormatter, configure_logging


def test_json_formatter_keeps_standard_and_extra_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="gpu-monitor.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.event_type = "unit_event"
    record.gpu_id = 1

    payload = json.loads(formatter.format(record))

    assert {"ts", "level", "logger", "message"}.issubset(payload)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "gpu-monitor.test"
    assert payload["message"] == "hello world"
    assert payload["event_type"] == "unit_event"
    assert payload["gpu_id"] == 1


def test_json_formatter_includes_exception() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        record = logging.getLogger("gpu-monitor.test").makeRecord(
            "gpu-monitor.test", logging.ERROR, __file__, 20, "failed", (), exc_info=(type(exc), exc, exc.__traceback__)
        )

    payload = json.loads(formatter.format(record))

    assert "ValueError" in payload["exception"]
    assert payload["message"] == "failed"


def test_configure_logging_selects_plain_formatter() -> None:
    configure_logging(LoggingConfig(level="INFO", structured=False))

    root = logging.getLogger()

    assert root.handlers
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)
