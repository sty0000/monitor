from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return _parse_bool(raw, default)


def _env_text(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _redact_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-2:]}"


@dataclass(frozen=True)
class MonitorConfig:
    instance_name: str = "gpu-monitor"
    interval_seconds: int = 15
    command_timeout_seconds: int = 8
    gpu_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class ThresholdConfig:
    usage_percent: float = 20
    idle_minutes: float = 10
    no_process_minutes: float = 5
    warmup_minutes: float = 3
    armed_stable_minutes: float = 1
    low_usage_mode: str = "any"
    primary_gpu_id: int | None = None


@dataclass(frozen=True)
class RecoveryConfig:
    enabled: bool = True
    cooldown_minutes: float = 5
    severe_only: bool = False


@dataclass(frozen=True)
class AlertConfig:
    cooldown_minutes: float = 30
    min_interval_minutes: float = 3
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


@dataclass(frozen=True)
class DashboardAuthConfig:
    enabled: bool = True
    token: str = ""
    require_auth_for_read: bool = False


@dataclass(frozen=True)
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8090
    auth: DashboardAuthConfig = field(default_factory=DashboardAuthConfig)


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    structured: bool = True
    event_log_path: str = "logs/events.jsonl"


@dataclass(frozen=True)
class MetricsConfig:
    enabled: bool = True


@dataclass(frozen=True)
class SMTPConfig:
    enabled: bool = False
    host: str = ""
    port: int = 465
    user: str = ""
    password: str = ""
    sender: str = ""
    to: list[str] = field(default_factory=list)
    use_tls: bool = False


@dataclass(frozen=True)
class WebhookConfig:
    enabled: bool = False
    url: str = ""


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    api_base: str = "https://api.telegram.org"


@dataclass(frozen=True)
class FeishuConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass(frozen=True)
class WecomConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass(frozen=True)
class DingtalkConfig:
    enabled: bool = False
    webhook_url: str = ""
    secret: str = ""


@dataclass(frozen=True)
class NotifyStrategyConfig:
    mode: str = "failover"
    order: list[str] = field(default_factory=lambda: ["wecom", "feishu", "dingtalk", "telegram"])
    fail_on_business_error: bool = False


@dataclass(frozen=True)
class NotifyControlConfig:
    enabled: bool = True


@dataclass(frozen=True)
class NotifyConfig:
    control: NotifyControlConfig = field(default_factory=NotifyControlConfig)
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    wecom: WecomConfig = field(default_factory=WecomConfig)
    dingtalk: DingtalkConfig = field(default_factory=DingtalkConfig)
    strategy: NotifyStrategyConfig = field(default_factory=NotifyStrategyConfig)


@dataclass(frozen=True)
class AppConfig:
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    threshold: ThresholdConfig = field(default_factory=ThresholdConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "monitor": {
                "instance_name": self.monitor.instance_name,
                "interval_seconds": self.monitor.interval_seconds,
                "command_timeout_seconds": self.monitor.command_timeout_seconds,
                "gpu_ids": self.monitor.gpu_ids,
            },
            "threshold": {
                "usage_percent": self.threshold.usage_percent,
                "idle_minutes": self.threshold.idle_minutes,
                "no_process_minutes": self.threshold.no_process_minutes,
                "warmup_minutes": self.threshold.warmup_minutes,
                "armed_stable_minutes": self.threshold.armed_stable_minutes,
                "low_usage_mode": self.threshold.low_usage_mode,
                "primary_gpu_id": self.threshold.primary_gpu_id,
            },
            "alert": {
                "cooldown_minutes": self.alert.cooldown_minutes,
                "min_interval_minutes": self.alert.min_interval_minutes,
                "recovery": {
                    "enabled": self.alert.recovery.enabled,
                    "cooldown_minutes": self.alert.recovery.cooldown_minutes,
                    "severe_only": self.alert.recovery.severe_only,
                },
            },
            "dashboard": {
                "host": self.dashboard.host,
                "port": self.dashboard.port,
                "auth": {
                    "enabled": self.dashboard.auth.enabled,
                    "require_auth_for_read": self.dashboard.auth.require_auth_for_read,
                    "token": _redact_secret(self.dashboard.auth.token),
                },
            },
            "logging": {
                "level": self.logging.level,
                "structured": self.logging.structured,
                "event_log_path": self.logging.event_log_path,
            },
            "metrics": {"enabled": self.metrics.enabled},
            "notify": {
                "control": {"enabled": self.notify.control.enabled},
                "strategy": {
                    "mode": self.notify.strategy.mode,
                    "order": self.notify.strategy.order,
                    "fail_on_business_error": self.notify.strategy.fail_on_business_error,
                },
                "smtp": {
                    "enabled": self.notify.smtp.enabled,
                    "host": self.notify.smtp.host,
                    "port": self.notify.smtp.port,
                    "user": self.notify.smtp.user,
                    "password": _redact_secret(self.notify.smtp.password),
                    "from": self.notify.smtp.sender,
                    "to": self.notify.smtp.to,
                    "use_tls": self.notify.smtp.use_tls,
                },
                "webhook": {
                    "enabled": self.notify.webhook.enabled,
                    "url": _redact_secret(self.notify.webhook.url),
                },
                "telegram": {
                    "enabled": self.notify.telegram.enabled,
                    "bot_token": _redact_secret(self.notify.telegram.bot_token),
                    "chat_id": _redact_secret(self.notify.telegram.chat_id),
                    "api_base": self.notify.telegram.api_base,
                },
                "feishu": {
                    "enabled": self.notify.feishu.enabled,
                    "webhook_url": _redact_secret(self.notify.feishu.webhook_url),
                },
                "wecom": {
                    "enabled": self.notify.wecom.enabled,
                    "webhook_url": _redact_secret(self.notify.wecom.webhook_url),
                },
                "dingtalk": {
                    "enabled": self.notify.dingtalk.enabled,
                    "webhook_url": _redact_secret(self.notify.dingtalk.webhook_url),
                    "secret": _redact_secret(self.notify.dingtalk.secret),
                },
            },
        }


def _parse_int_list(values: Any) -> list[int]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ConfigError("gpu_ids must be a list")
    return [int(item) for item in values]


def _validate(config: AppConfig) -> AppConfig:
    if config.monitor.interval_seconds <= 0:
        raise ConfigError("monitor.interval_seconds must be > 0")
    if config.monitor.command_timeout_seconds <= 0:
        raise ConfigError("monitor.command_timeout_seconds must be > 0")
    if config.dashboard.port <= 0:
        raise ConfigError("dashboard.port must be > 0")
    if config.threshold.low_usage_mode not in {"any", "all", "majority", "selected_primary"}:
        raise ConfigError("threshold.low_usage_mode must be one of any/all/majority/selected_primary")
    if config.dashboard.auth.enabled and not config.dashboard.auth.token:
        raise ConfigError("dashboard.auth.token is required when dashboard.auth.enabled=true")
    return config


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    monitor = raw.get("monitor", {})
    threshold = raw.get("threshold", {})
    alert = raw.get("alert", {})
    dashboard = raw.get("dashboard", {})
    notify = raw.get("notify", {})
    logging_cfg = raw.get("logging", {})
    metrics = raw.get("metrics", {})

    recovery = alert.get("recovery", {})
    dashboard_auth = dashboard.get("auth", {})
    notify_control = notify.get("control", {})
    strategy = notify.get("strategy", {})
    smtp = notify.get("smtp", {})
    webhook = notify.get("webhook", {})
    telegram = notify.get("telegram", {})
    feishu = notify.get("feishu", {})
    wecom = notify.get("wecom", {})
    dingtalk = notify.get("dingtalk", {})

    config = AppConfig(
        monitor=MonitorConfig(
            instance_name=str(monitor.get("instance_name", "gpu-monitor")),
            interval_seconds=int(monitor.get("interval_seconds", 15)),
            command_timeout_seconds=int(monitor.get("command_timeout_seconds", 8)),
            gpu_ids=_parse_int_list(monitor.get("gpu_ids", [])),
        ),
        threshold=ThresholdConfig(
            usage_percent=float(threshold.get("usage_percent", 20)),
            idle_minutes=float(threshold.get("idle_minutes", 10)),
            no_process_minutes=float(threshold.get("no_process_minutes", 5)),
            warmup_minutes=float(threshold.get("warmup_minutes", 3)),
            armed_stable_minutes=float(threshold.get("armed_stable_minutes", 1)),
            low_usage_mode=str(threshold.get("low_usage_mode", "any")),
            primary_gpu_id=int(threshold["primary_gpu_id"]) if threshold.get("primary_gpu_id") is not None else None,
        ),
        alert=AlertConfig(
            cooldown_minutes=float(alert.get("cooldown_minutes", 30)),
            min_interval_minutes=float(alert.get("min_interval_minutes", 3)),
            recovery=RecoveryConfig(
                enabled=_parse_bool(recovery.get("enabled", True), True),
                cooldown_minutes=float(recovery.get("cooldown_minutes", 5)),
                severe_only=_parse_bool(recovery.get("severe_only", False), False),
            ),
        ),
        dashboard=DashboardConfig(
            host=_env_text("GPU_MONITOR_DASHBOARD_HOST", str(dashboard.get("host", "127.0.0.1"))),
            port=int(_env_text("GPU_MONITOR_DASHBOARD_PORT", str(dashboard.get("port", 8090)))),
            auth=DashboardAuthConfig(
                enabled=_parse_bool(dashboard_auth.get("enabled", True), True),
                token=_env_text("GPU_MONITOR_DASHBOARD_AUTH_TOKEN", str(dashboard_auth.get("token", ""))),
                require_auth_for_read=_parse_bool(dashboard_auth.get("require_auth_for_read", False), False),
            ),
        ),
        logging=LoggingConfig(
            level=str(logging_cfg.get("level", "INFO")).upper(),
            structured=_parse_bool(logging_cfg.get("structured", True), True),
            event_log_path=str(logging_cfg.get("event_log_path", "logs/events.jsonl")),
        ),
        metrics=MetricsConfig(enabled=_parse_bool(metrics.get("enabled", True), True)),
        notify=NotifyConfig(
            control=NotifyControlConfig(
                enabled=_env_bool("GPU_MONITOR_NOTIFY_ENABLED", _parse_bool(notify_control.get("enabled", True), True))
            ),
            smtp=SMTPConfig(
                enabled=_parse_bool(smtp.get("enabled", False), False),
                host=str(smtp.get("host", "")),
                port=int(smtp.get("port", 465)),
                user=str(smtp.get("user", "")),
                password=str(smtp.get("password", "")),
                sender=str(smtp.get("from", smtp.get("sender", ""))),
                to=[str(item) for item in smtp.get("to", [])],
                use_tls=_parse_bool(smtp.get("use_tls", False), False),
            ),
            webhook=WebhookConfig(
                enabled=_parse_bool(webhook.get("enabled", False), False),
                url=str(webhook.get("url", "")),
            ),
            telegram=TelegramConfig(
                enabled=_parse_bool(telegram.get("enabled", False), False),
                bot_token=str(telegram.get("bot_token", "")),
                chat_id=str(telegram.get("chat_id", "")),
                api_base=str(telegram.get("api_base", "https://api.telegram.org")),
            ),
            feishu=FeishuConfig(
                enabled=_parse_bool(feishu.get("enabled", False), False),
                webhook_url=str(feishu.get("webhook_url", "")),
            ),
            wecom=WecomConfig(
                enabled=_parse_bool(wecom.get("enabled", False), False),
                webhook_url=str(wecom.get("webhook_url", "")),
            ),
            dingtalk=DingtalkConfig(
                enabled=_parse_bool(dingtalk.get("enabled", False), False),
                webhook_url=str(dingtalk.get("webhook_url", "")),
                secret=str(dingtalk.get("secret", "")),
            ),
            strategy=NotifyStrategyConfig(
                mode=str(strategy.get("mode", "failover")),
                order=[str(item) for item in strategy.get("order", ["wecom", "feishu", "dingtalk", "telegram"])],
                fail_on_business_error=_parse_bool(strategy.get("fail_on_business_error", False), False),
            ),
        ),
    )
    return _validate(config)
