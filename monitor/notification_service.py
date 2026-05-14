from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .notifiers.dingtalk_notifier import DingtalkNotifier
from .notifiers.exceptions import BusinessError, NotificationError
from .notifiers.feishu_notifier import FeishuNotifier
from .notifiers.smtp_notifier import SMTPNotifier
from .notifiers.telegram_notifier import TelegramNotifier
from .notifiers.webhook_notifier import WebhookNotifier
from .notifiers.wecom_notifier import WecomNotifier

LOGGER = logging.getLogger("gpu-monitor")


@dataclass(frozen=True)
class NotificationResult:
    notifier: str
    attempted: list[str]


class NotificationService:
    def __init__(self, ordered_notifiers: list[tuple[str, Any]], fail_on_business_error: bool) -> None:
        self.ordered_notifiers = ordered_notifiers
        self.fail_on_business_error = fail_on_business_error

    @property
    def active_channels(self) -> list[str]:
        return [name for name, _ in self.ordered_notifiers]

    def send(self, subject: str, body: str, channel_name: str | None = None) -> NotificationResult:
        if channel_name:
            targets = [(name, notifier) for name, notifier in self.ordered_notifiers if name == channel_name]
            if not targets:
                raise NotificationError(f"Notifier not configured: {channel_name}")
        else:
            targets = self.ordered_notifiers

        if not targets:
            raise NotificationError("No notifier configured in failover chain")

        attempted: list[str] = []
        errors: list[str] = []
        for name, notifier in targets:
            attempted.append(name)
            try:
                notifier.send(subject, body)
                LOGGER.info("Notifier succeeded", extra={"event_type": "notify_sent", "notifier": name})
                return NotificationResult(notifier=name, attempted=attempted)
            except BusinessError as exc:
                errors.append(f"{name} business error: {exc}")
                LOGGER.warning("Notifier business error", extra={"event_type": "notify_business_error", "notifier": name})
                if channel_name or not self.fail_on_business_error:
                    raise
            except NotificationError as exc:
                errors.append(f"{name} failed: {exc}")
                LOGGER.warning("Notifier transport/http error", extra={"event_type": "notify_error", "notifier": name})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name} unexpected error: {exc}")
                LOGGER.warning("Notifier unexpected error", extra={"event_type": "notify_error", "notifier": name})
        raise NotificationError("All notifiers failed: " + " | ".join(errors))


def build_notification_service(config: AppConfig) -> NotificationService:
    notifiers: dict[str, Any] = {}
    fail_on_business_error = config.notify.strategy.fail_on_business_error

    smtp = config.notify.smtp
    if smtp.enabled and smtp.host and smtp.sender and smtp.to:
        notifiers["smtp"] = SMTPNotifier(
            host=smtp.host,
            port=smtp.port,
            user=smtp.user,
            password=smtp.password,
            sender=smtp.sender,
            recipients=smtp.to,
            use_tls=smtp.use_tls,
        )

    webhook = config.notify.webhook
    if webhook.enabled and webhook.url:
        notifiers["webhook"] = WebhookNotifier(url=webhook.url)

    telegram = config.notify.telegram
    if telegram.enabled and telegram.bot_token and telegram.chat_id:
        notifiers["telegram"] = TelegramNotifier(
            bot_token=telegram.bot_token,
            chat_id=telegram.chat_id,
            api_base=telegram.api_base,
        )

    feishu = config.notify.feishu
    if feishu.enabled and feishu.webhook_url:
        notifiers["feishu"] = FeishuNotifier(webhook_url=feishu.webhook_url)

    wecom = config.notify.wecom
    if wecom.enabled and wecom.webhook_url:
        notifiers["wecom"] = WecomNotifier(webhook_url=wecom.webhook_url)

    dingtalk = config.notify.dingtalk
    if dingtalk.enabled and dingtalk.webhook_url:
        notifiers["dingtalk"] = DingtalkNotifier(webhook_url=dingtalk.webhook_url, secret=dingtalk.secret)

    ordered: list[tuple[str, Any]] = []
    for name in config.notify.strategy.order:
        notifier = notifiers.get(name)
        if notifier is not None:
            ordered.append((name, notifier))
    for name, notifier in notifiers.items():
        if name not in config.notify.strategy.order:
            ordered.append((name, notifier))
    return NotificationService(ordered, fail_on_business_error)

