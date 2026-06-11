from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


KNOWN_NOTIFY_CHANNELS = {"smtp", "webhook", "telegram", "feishu", "wecom", "dingtalk"}


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
class PlatformConfig:
    profile: str = "auto"
    telemetry_order: list[str] = field(default_factory=lambda: ["dcgm", "nvidia_smi"])


@dataclass(frozen=True)
class ThresholdConfig:
    usage_percent: float = 20
    idle_minutes: float = 10
    no_process_minutes: float = 5
    high_temperature_c: float = 85
    high_temperature_minutes: float = 3
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
class RuntimeErrorAlertConfig:
    enabled: bool = True
    consecutive_failures: int = 3
    cooldown_minutes: float = 30


@dataclass(frozen=True)
class GpuErrorAlertConfig:
    enabled: bool = True
    muted_gpu_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class AlertConfig:
    cooldown_minutes: float = 30
    min_interval_minutes: float = 3
    runtime_error: RuntimeErrorAlertConfig = field(default_factory=RuntimeErrorAlertConfig)
    gpu_error: GpuErrorAlertConfig = field(default_factory=GpuErrorAlertConfig)
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
class HistoryConfig:
    enabled: bool = True
    path: str = "logs/history.jsonl"
    max_points_memory: int = 1440
    max_file_mb: float = 50
    persist_interval_seconds: int = 15
    retention_days: int = 30
    rotate_keep_files: int = 5
    query_default_limit: int = 240


@dataclass(frozen=True)
class MetricsConfig:
    enabled: bool = True


@dataclass(frozen=True)
class ScenarioMessagesConfig:
    low_usage_hint: str = ""
    no_process_hint: str = ""


@dataclass(frozen=True)
class HubConfig:
    auth_token: str = ""


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
class NotifyTemplateConfig:
    subject: str = ""
    body: str = ""
    body_format: str = "plain"


@dataclass(frozen=True)
class AlertEscalationRuleConfig:
    alert: str
    after_minutes: float
    channel: str
    cooldown_minutes: float = 30


@dataclass(frozen=True)
class AlertEscalationConfig:
    enabled: bool = False
    rules: list[AlertEscalationRuleConfig] = field(default_factory=list)


@dataclass(frozen=True)
class NotifyControlConfig:
    enabled: bool = True
    low_usage_enabled: bool = True


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
    templates: dict[str, NotifyTemplateConfig] = field(default_factory=dict)
    escalation: AlertEscalationConfig = field(default_factory=AlertEscalationConfig)


@dataclass(frozen=True)
class AppConfig:
    scenario_profile: str = "custom"
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    platform: PlatformConfig = field(default_factory=PlatformConfig)
    threshold: ThresholdConfig = field(default_factory=ThresholdConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    scenario_messages: ScenarioMessagesConfig = field(default_factory=ScenarioMessagesConfig)
    profile_templates: dict[str, Any] = field(default_factory=dict)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.dashboard.host == "0.0.0.0" and not self.dashboard.auth.enabled:
            warnings.append("dashboard binds 0.0.0.0 with auth disabled")
        if self.dashboard.host == "0.0.0.0" and self.dashboard.auth.enabled and not self.dashboard.auth.require_auth_for_read:
            warnings.append("dashboard reads are public on 0.0.0.0; consider require_auth_for_read=true")
        return warnings

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "scenario_profile": self.scenario_profile,
            "monitor": {
                "instance_name": self.monitor.instance_name,
                "interval_seconds": self.monitor.interval_seconds,
                "command_timeout_seconds": self.monitor.command_timeout_seconds,
                "gpu_ids": self.monitor.gpu_ids,
            },
            "platform": {
                "profile": self.platform.profile,
                "telemetry_order": self.platform.telemetry_order,
            },
            "threshold": {
                "usage_percent": self.threshold.usage_percent,
                "idle_minutes": self.threshold.idle_minutes,
                "no_process_minutes": self.threshold.no_process_minutes,
                "high_temperature_c": self.threshold.high_temperature_c,
                "high_temperature_minutes": self.threshold.high_temperature_minutes,
                "warmup_minutes": self.threshold.warmup_minutes,
                "armed_stable_minutes": self.threshold.armed_stable_minutes,
                "low_usage_mode": self.threshold.low_usage_mode,
                "primary_gpu_id": self.threshold.primary_gpu_id,
            },
            "scenario_messages": {
                "low_usage_hint": self.scenario_messages.low_usage_hint,
                "no_process_hint": self.scenario_messages.no_process_hint,
            },
            "alert": {
                "cooldown_minutes": self.alert.cooldown_minutes,
                "min_interval_minutes": self.alert.min_interval_minutes,
                "runtime_error": {
                    "enabled": self.alert.runtime_error.enabled,
                    "consecutive_failures": self.alert.runtime_error.consecutive_failures,
                    "cooldown_minutes": self.alert.runtime_error.cooldown_minutes,
                },
                "gpu_error": {
                    "enabled": self.alert.gpu_error.enabled,
                    "muted_gpu_ids": self.alert.gpu_error.muted_gpu_ids,
                },
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
                "control": {
                    "enabled": self.notify.control.enabled,
                    "low_usage_enabled": self.notify.control.low_usage_enabled,
                },
                "strategy": {
                    "mode": self.notify.strategy.mode,
                    "order": self.notify.strategy.order,
                    "fail_on_business_error": self.notify.strategy.fail_on_business_error,
                },
                "templates": sorted(self.notify.templates),
                "escalation": {
                    "enabled": self.notify.escalation.enabled,
                    "rules": [
                        {"alert": rule.alert, "after_minutes": rule.after_minutes, "channel": rule.channel, "cooldown_minutes": rule.cooldown_minutes}
                        for rule in self.notify.escalation.rules
                    ],
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
    from .profile_templates import available_profile_templates

    templates = available_profile_templates(config.profile_templates)
    if config.scenario_profile not in templates:
        raise ConfigError("unknown scenario_profile: " + config.scenario_profile + "; use a built-in profile or define it in profile_templates")
    if config.monitor.interval_seconds <= 0:
        raise ConfigError("monitor.interval_seconds must be > 0")
    if config.monitor.command_timeout_seconds <= 0:
        raise ConfigError("monitor.command_timeout_seconds must be > 0")
    if config.dashboard.port <= 0:
        raise ConfigError("dashboard.port must be > 0")
    if config.platform.profile not in {"auto", "generic_nvidia", "dgx_spark"}:
        raise ConfigError("platform.profile must be one of auto/generic_nvidia/dgx_spark")
    if not config.platform.telemetry_order:
        raise ConfigError("platform.telemetry_order must not be empty")
    invalid_sources = set(config.platform.telemetry_order) - {"nvml", "dcgm", "nvidia_smi"}
    if invalid_sources:
        raise ConfigError("platform.telemetry_order must contain only nvml/dcgm/nvidia_smi")
    if config.threshold.low_usage_mode not in {"any", "all", "majority", "selected_primary"}:
        raise ConfigError("threshold.low_usage_mode must be one of any/all/majority/selected_primary")
    if config.dashboard.auth.enabled and not config.dashboard.auth.token:
        raise ConfigError("dashboard.auth.token is required when dashboard.auth.enabled=true")
    if not config.logging.event_log_path.strip():
        raise ConfigError("logging.event_log_path must not be empty")
    if not config.history.path.strip():
        raise ConfigError("history.path must not be empty")
    if config.alert.cooldown_minutes < 0:
        raise ConfigError("alert.cooldown_minutes must be >= 0")
    if config.alert.min_interval_minutes < 0:
        raise ConfigError("alert.min_interval_minutes must be >= 0")
    if config.alert.runtime_error.cooldown_minutes < 0:
        raise ConfigError("alert.runtime_error.cooldown_minutes must be >= 0")
    if config.alert.runtime_error.consecutive_failures <= 0:
        raise ConfigError("alert.runtime_error.consecutive_failures must be > 0")
    if config.history.max_points_memory <= 0:
        raise ConfigError("history.max_points_memory must be > 0")
    if config.history.max_file_mb <= 0:
        raise ConfigError("history.max_file_mb must be > 0")
    if config.history.persist_interval_seconds <= 0:
        raise ConfigError("history.persist_interval_seconds must be > 0")
    if config.history.retention_days < 0:
        raise ConfigError("history.retention_days must be >= 0")
    if config.history.rotate_keep_files < 0:
        raise ConfigError("history.rotate_keep_files must be >= 0")
    if config.history.query_default_limit <= 0:
        raise ConfigError("history.query_default_limit must be > 0")
    if config.notify.strategy.mode != "failover":
        raise ConfigError("notify.strategy.mode must be failover")
    if not config.notify.strategy.order:
        raise ConfigError("notify.strategy.order must not be empty")
    invalid_channels = set(config.notify.strategy.order) - KNOWN_NOTIFY_CHANNELS
    if invalid_channels:
        raise ConfigError("notify.strategy.order contains unknown channel(s): " + ", ".join(sorted(invalid_channels)))
    for alert_key, template in config.notify.templates.items():
        if template.body_format not in {"plain", "markdown"}:
            raise ConfigError(f"notify.templates.{alert_key}.body_format must be plain or markdown")
    for rule in config.notify.escalation.rules:
        if rule.after_minutes < 0:
            raise ConfigError("notify.escalation.rules.after_minutes must be >= 0")
        if rule.cooldown_minutes < 0:
            raise ConfigError("notify.escalation.rules.cooldown_minutes must be >= 0")
        if rule.channel not in KNOWN_NOTIFY_CHANNELS:
            raise ConfigError("notify.escalation.rules.channel contains unknown channel: " + rule.channel)
    return config


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    monitor = raw.get("monitor", {})
    platform = raw.get("platform", {})
    threshold = raw.get("threshold", {})
    alert = raw.get("alert", {})
    dashboard = raw.get("dashboard", {})
    notify = raw.get("notify", {})
    logging_cfg = raw.get("logging", {})
    metrics = raw.get("metrics", {})
    history = raw.get("history", {})
    scenario_messages = raw.get("scenario_messages", {})

    recovery = alert.get("recovery", {})
    runtime_error = alert.get("runtime_error", {})
    gpu_error = alert.get("gpu_error", {})
    dashboard_auth = dashboard.get("auth", {})
    notify_control = notify.get("control", {})
    strategy = notify.get("strategy", {})
    templates = notify.get("templates", {})
    escalation = notify.get("escalation", {})
    smtp = notify.get("smtp", {})
    webhook = notify.get("webhook", {})
    telegram = notify.get("telegram", {})
    feishu = notify.get("feishu", {})
    wecom = notify.get("wecom", {})
    dingtalk = notify.get("dingtalk", {})

    config = AppConfig(
        scenario_profile=str(raw.get("scenario_profile", "custom")),
        monitor=MonitorConfig(
            instance_name=str(monitor.get("instance_name", "gpu-monitor")),
            interval_seconds=int(monitor.get("interval_seconds", 15)),
            command_timeout_seconds=int(monitor.get("command_timeout_seconds", 8)),
            gpu_ids=_parse_int_list(monitor.get("gpu_ids", [])),
        ),
        platform=PlatformConfig(
            profile=str(platform.get("profile", "auto")),
            telemetry_order=[str(item) for item in platform.get("telemetry_order", ["dcgm", "nvidia_smi"])],
        ),
        threshold=ThresholdConfig(
            usage_percent=float(threshold.get("usage_percent", 20)),
            idle_minutes=float(threshold.get("idle_minutes", 10)),
            no_process_minutes=float(threshold.get("no_process_minutes", 5)),
            high_temperature_c=float(threshold.get("high_temperature_c", 85)),
            high_temperature_minutes=float(threshold.get("high_temperature_minutes", 3)),
            warmup_minutes=float(threshold.get("warmup_minutes", 3)),
            armed_stable_minutes=float(threshold.get("armed_stable_minutes", 1)),
            low_usage_mode=str(threshold.get("low_usage_mode", "any")),
            primary_gpu_id=int(threshold["primary_gpu_id"]) if threshold.get("primary_gpu_id") is not None else None,
        ),
        alert=AlertConfig(
            cooldown_minutes=float(alert.get("cooldown_minutes", 30)),
            min_interval_minutes=float(alert.get("min_interval_minutes", 3)),
            runtime_error=RuntimeErrorAlertConfig(
                enabled=_parse_bool(runtime_error.get("enabled", True), True),
                consecutive_failures=int(runtime_error.get("consecutive_failures", 3)),
                cooldown_minutes=float(runtime_error.get("cooldown_minutes", 30)),
            ),
            gpu_error=GpuErrorAlertConfig(
                enabled=_parse_bool(gpu_error.get("enabled", True), True),
                muted_gpu_ids=_parse_int_list(gpu_error.get("muted_gpu_ids", [])),
            ),
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
        history=HistoryConfig(
            enabled=_parse_bool(history.get("enabled", True), True),
            path=str(history.get("path", "logs/history.jsonl")),
            max_points_memory=int(history.get("max_points_memory", 1440)),
            max_file_mb=float(history.get("max_file_mb", 50)),
            persist_interval_seconds=int(history.get("persist_interval_seconds", 15)),
            retention_days=int(history.get("retention_days", 30)),
            rotate_keep_files=int(history.get("rotate_keep_files", 5)),
            query_default_limit=int(history.get("query_default_limit", 240)),
        ),
        scenario_messages=ScenarioMessagesConfig(
            low_usage_hint=str(scenario_messages.get("low_usage_hint", "")),
            no_process_hint=str(scenario_messages.get("no_process_hint", "")),
        ),
        profile_templates=dict(raw.get("profile_templates", {}) or {}),
        notify=NotifyConfig(
            control=NotifyControlConfig(
                enabled=_env_bool("GPU_MONITOR_NOTIFY_ENABLED", _parse_bool(notify_control.get("enabled", True), True)),
                low_usage_enabled=_env_bool(
                    "GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED",
                    _parse_bool(notify_control.get("low_usage_enabled", True), True),
                ),
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
            templates={
                str(key): NotifyTemplateConfig(
                    subject=str(value.get("subject", "")) if isinstance(value, dict) else "",
                    body=str(value.get("body", "")) if isinstance(value, dict) else "",
                    body_format=str(value.get("body_format", "plain")) if isinstance(value, dict) else "plain",
                )
                for key, value in (templates.items() if isinstance(templates, dict) else [])
            },
            escalation=AlertEscalationConfig(
                enabled=_parse_bool(escalation.get("enabled", False), False) if isinstance(escalation, dict) else False,
                rules=[
                    AlertEscalationRuleConfig(
                        alert=str(rule.get("alert", "")),
                        after_minutes=float(rule.get("after_minutes", 0)),
                        channel=str(rule.get("channel", "")),
                        cooldown_minutes=float(rule.get("cooldown_minutes", 30)),
                    )
                    for rule in (escalation.get("rules", []) if isinstance(escalation, dict) else [])
                    if isinstance(rule, dict)
                ],
            ),
        ),
    )
    return _validate(config)
