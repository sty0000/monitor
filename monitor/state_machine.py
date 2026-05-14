from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import AppConfig

SEVERE_ALERTS = {"NO_PROCESS_ALERT"}


@dataclass
class MonitorState:
    start_ts: float
    armed: bool = False
    armed_since: float | None = None
    low_usage_since: float | None = None
    no_process_since: float | None = None
    active_alert: str | None = None
    last_alert_sent_at: dict[str, float] = field(default_factory=dict)
    last_any_alert_sent_at: float | None = None


@dataclass(frozen=True)
class EvaluationResult:
    state_name: str
    alert_key: str | None
    reason: str


def should_send_with_cooldown(state: MonitorState, alert_key: str, cooldown_seconds: float, now: float | None = None) -> bool:
    current = now or time.time()
    last_ts = state.last_alert_sent_at.get(alert_key)
    return last_ts is None or (current - last_ts) >= cooldown_seconds


def should_send_by_global_interval(state: MonitorState, min_interval_seconds: float, now: float | None = None) -> bool:
    if min_interval_seconds <= 0:
        return True
    current = now or time.time()
    return state.last_any_alert_sent_at is None or (current - state.last_any_alert_sent_at) >= min_interval_seconds


def mark_alert_sent(state: MonitorState, alert_key: str, now: float | None = None) -> None:
    current = now or time.time()
    state.last_alert_sent_at[alert_key] = current
    state.last_any_alert_sent_at = current


def can_send_recovery(config: AppConfig, previous_alert: str | None) -> bool:
    if not previous_alert:
        return False
    if not config.alert.recovery.enabled:
        return False
    if config.alert.recovery.severe_only and previous_alert not in SEVERE_ALERTS:
        return False
    return True


def _is_low_usage(gpus: list[dict[str, Any]], config: AppConfig) -> dict[str, Any] | None:
    threshold = config.threshold.usage_percent
    candidates = [gpu for gpu in gpus if gpu.get("utilization_gpu", -1) >= 0]
    if not candidates:
        return None

    mode = config.threshold.low_usage_mode
    if mode == "any":
        for gpu in candidates:
            if gpu.get("utilization_gpu", -1) < threshold:
                return gpu
        return None

    if mode == "all":
        if all(gpu.get("utilization_gpu", -1) < threshold for gpu in candidates):
            return candidates[0]
        return None

    if mode == "majority":
        matched = [gpu for gpu in candidates if gpu.get("utilization_gpu", -1) < threshold]
        return matched[0] if len(matched) > len(candidates) / 2 else None

    primary_gpu_id = config.threshold.primary_gpu_id
    if primary_gpu_id is None:
        primary_gpu_id = config.monitor.gpu_ids[0] if config.monitor.gpu_ids else candidates[0].get("index")
    for gpu in candidates:
        if gpu.get("index") == primary_gpu_id and gpu.get("utilization_gpu", -1) < threshold:
            return gpu
    return None


def evaluate_state(config: AppConfig, state: MonitorState, sample: dict[str, Any], now: float | None = None) -> EvaluationResult:
    current = now or time.time()
    warmup_seconds = config.threshold.warmup_minutes * 60
    idle_seconds = config.threshold.idle_minutes * 60
    no_process_seconds = config.threshold.no_process_minutes * 60
    armed_stable_seconds = config.threshold.armed_stable_minutes * 60

    gpus = sample.get("gpus", [])
    all_pids = [pid for gpu in gpus for pid in gpu.get("compute_pids", [])]
    has_compute = bool(all_pids)

    if (current - state.start_ts) < warmup_seconds:
        state.low_usage_since = None
        state.no_process_since = None
        state.armed_since = None
        return EvaluationResult("WARMUP", None, "still in warmup window")

    if has_compute and not state.armed:
        if state.armed_since is None:
            state.armed_since = current
        if (current - state.armed_since) < armed_stable_seconds:
            state.low_usage_since = None
            state.no_process_since = None
            return EvaluationResult("ARMING", None, "compute process detected, waiting for stable armed window")
        state.armed = True

    if not has_compute and not state.armed:
        state.armed_since = None
        state.low_usage_since = None
        state.no_process_since = None
        return EvaluationResult("WAITING_ACTIVE", None, "waiting for first compute process")

    if not has_compute:
        state.low_usage_since = None
        if state.no_process_since is None:
            state.no_process_since = current
        if (current - state.no_process_since) >= no_process_seconds:
            return EvaluationResult("NO_PROCESS_ALERT", "NO_PROCESS_ALERT", "armed but no compute processes for threshold duration")
        return EvaluationResult("ACTIVE", None, "armed but currently no compute process")

    state.no_process_since = None
    low_usage_gpu = _is_low_usage(gpus, config)
    if low_usage_gpu is not None:
        if state.low_usage_since is None:
            state.low_usage_since = current
        if (current - state.low_usage_since) >= idle_seconds:
            details = (
                f"gpu={low_usage_gpu['index']} util={low_usage_gpu['utilization_gpu']}% "
                f"threshold={config.threshold.usage_percent}% mode={config.threshold.low_usage_mode}"
            )
            return EvaluationResult("LOW_USAGE_ALERT", "LOW_USAGE_ALERT", details)
        return EvaluationResult("ACTIVE", None, "compute process exists but low-util duration not enough")

    state.low_usage_since = None
    return EvaluationResult("ACTIVE", None, "healthy")


def build_alert_message(instance_name: str, alert_key: str, sample: dict[str, Any], reason: str) -> tuple[str, str]:
    title = f"[GPU Monitor][{instance_name}] {alert_key}"
    lines = [f"monitor: {instance_name}", f"time(utc): {sample['timestamp']}", f"alert: {alert_key}", f"reason: {reason}", "", "gpu snapshot:"]
    for gpu in sample.get("gpus", []):
        lines.append(
            f"- gpu{gpu['index']}: util={gpu['utilization_gpu']}%, mem={gpu['memory_used_mb']}MB, power={gpu['power_draw_w']}W, temp={gpu['temperature_c']}C, pids={gpu['compute_pids']}"
        )
    return title, "\n".join(lines)


def build_recovered_message(instance_name: str, previous_alert: str, sample: dict[str, Any]) -> tuple[str, str]:
    title = f"[GPU Monitor][{instance_name}] RECOVERED"
    lines = [
        f"monitor: {instance_name}",
        f"time(utc): {sample['timestamp']}",
        f"recovered_from: {previous_alert}",
        "status: active and healthy",
        "",
        "gpu snapshot:",
    ]
    for gpu in sample.get("gpus", []):
        lines.append(
            f"- gpu{gpu['index']}: util={gpu['utilization_gpu']}%, mem={gpu['memory_used_mb']}MB, power={gpu['power_draw_w']}W, temp={gpu['temperature_c']}C, pids={gpu['compute_pids']}"
        )
    return title, "\n".join(lines)
