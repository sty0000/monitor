from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger("gpu-monitor")


class FailoverNotifier:
    def __init__(self, ordered_notifiers: list[tuple[str, Any]]) -> None:
        self.ordered_notifiers = ordered_notifiers

    def send(self, subject: str, body: str) -> None:
        if not self.ordered_notifiers:
            raise RuntimeError("No notifier configured in failover chain")

        errors: list[str] = []
        for name, notifier in self.ordered_notifiers:
            try:
                notifier.send(subject, body)
                LOGGER.info("Notifier succeeded: %s", name)
                return
            except Exception as exc:  # noqa: BLE001
                msg = f"{name} failed: {exc}"
                errors.append(msg)
                LOGGER.warning(msg)
        raise RuntimeError("All notifiers failed: " + " | ".join(errors))
