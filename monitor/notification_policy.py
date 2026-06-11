from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any

from .config import AlertEscalationRuleConfig, AppConfig
from .state_machine import MonitorState, should_send_with_cooldown


RECOVERY_ALERT_KEYS = {
    "HIGH_TEMPERATURE_ALERT": "HIGH_TEMPERATURE_RECOVERED",
    "GPU_ERROR_ALERT": "GPU_ERROR_RECOVERED",
    "RUNTIME_ERROR_ALERT": "RUNTIME_ERROR_RECOVERED",
    "LOW_USAGE_ALERT": "LOW_USAGE_RECOVERED",
    "NO_PROCESS_ALERT": "LOW_USAGE_RECOVERED",
}


class SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def safe_format(template: str, values: dict[str, Any]) -> str:
    try:
        return Formatter().vformat(template, (), SafeFormatDict(values))
    except Exception:
        return template


def recovery_alert_key(previous_alert: str) -> str:
    return RECOVERY_ALERT_KEYS.get(previous_alert, f"RECOVERED:{previous_alert}")


def apply_message_template(config: AppConfig, alert_key: str, subject: str, body: str, values: dict[str, Any]) -> tuple[str, str]:
    template = config.notify.templates.get(alert_key)
    if template is None:
        return subject, body
    rendered_subject = safe_format(template.subject, values) if template.subject else subject
    rendered_body = safe_format(template.body, values) if template.body else body
    return rendered_subject, rendered_body


@dataclass(frozen=True)
class EscalationDecision:
    rule: AlertEscalationRuleConfig
    key: str


def find_escalation(config: AppConfig, state: MonitorState, alert_key: str, now: float) -> EscalationDecision | None:
    if not config.notify.escalation.enabled:
        return None
    active_since = state.alert_first_seen_at.get(alert_key)
    if active_since is None:
        return None
    for rule in config.notify.escalation.rules:
        if rule.alert != alert_key:
            continue
        if now - active_since < rule.after_minutes * 60:
            continue
        escalation_key = f"ESCALATED:{alert_key}:{rule.channel}"
        if not should_send_with_cooldown(state, escalation_key, rule.cooldown_minutes * 60, now):
            continue
        return EscalationDecision(rule=rule, key=escalation_key)
    return None
