from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Info, generate_latest

from . import __version__
from .collector import CollectorError, GPUCollector
from .config import AppConfig, load_config
from .notification_service import NotificationError, build_notification_service
from .notification_policy import apply_message_template, find_escalation, recovery_alert_key
from .telemetry_schema import ADVANCED_TELEMETRY_FIELDS, normalize_advanced_telemetry
from .state_machine import (
    EvaluationResult,
    MonitorState,
    build_alert_message,
    build_recovered_message,
    can_send_recovery,
    evaluate_state,
    mark_alert_sent,
    should_send_by_global_interval,
    should_send_with_cooldown,
)

LOGGER = logging.getLogger("gpu-monitor")


@dataclass
class RuntimeCommand:
    action: str
    payload: dict[str, Any]
    response: queue.Queue[Any]


class RuntimeMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.up = Gauge("gpu_monitor_up", "Runtime liveness", registry=self.registry)
        self.notify_enabled = Gauge("gpu_monitor_notify_enabled", "Notification master switch", registry=self.registry)
        self.last_sample_ts = Gauge("gpu_monitor_last_sample_timestamp_seconds", "Last sample timestamp", registry=self.registry)
        self.consecutive_failures = Gauge("gpu_monitor_consecutive_failures", "Consecutive collection failures", registry=self.registry)
        self.state_code = Gauge("gpu_monitor_state_code", "Runtime state code", registry=self.registry)
        self.state_info = Info("gpu_monitor_state", "Runtime state details", registry=self.registry)
        self.version_info = Info("gpu_monitor_version", "Monitor version", registry=self.registry)
        self.version_info.info({"version": __version__})
        self.alert_total = Counter("gpu_monitor_alert_total", "Alerts emitted", ["alert_key"], registry=self.registry)
        self.notify_total = Counter("gpu_monitor_notify_total", "Notification attempts", ["notifier", "outcome"], registry=self.registry)
        self.collection_errors_total = Counter("gpu_monitor_collection_errors_total", "Collection errors", ["error_type"], registry=self.registry)
        self.history_points = Gauge("gpu_monitor_history_points", "History points kept in memory", registry=self.registry)
        self.history_events = Gauge("gpu_monitor_history_events", "Runtime events kept in memory", registry=self.registry)
        self.acknowledged_alerts = Gauge("gpu_monitor_acknowledged_alerts", "Acknowledged active alerts", registry=self.registry)
        self.silenced_alerts = Gauge("gpu_monitor_silenced_alerts", "Silenced alerts", ["mode"], registry=self.registry)
        self.notifier_health = Gauge("gpu_monitor_notifier_health", "Notifier health status", ["notifier"], registry=self.registry)
        self.gpu_utilization = Gauge("gpu_monitor_gpu_utilization_percent", "GPU utilization percent", ["gpu", "uuid"], registry=self.registry)
        self.gpu_memory_used = Gauge("gpu_monitor_gpu_memory_used_mb", "GPU memory used in MiB", ["gpu", "uuid"], registry=self.registry)
        self.gpu_memory_total = Gauge("gpu_monitor_gpu_memory_total_mb", "GPU memory total in MiB", ["gpu", "uuid"], registry=self.registry)
        self.gpu_power_draw = Gauge("gpu_monitor_gpu_power_draw_watts", "GPU power draw in watts", ["gpu", "uuid"], registry=self.registry)
        self.gpu_temperature = Gauge("gpu_monitor_gpu_temperature_celsius", "GPU temperature in Celsius", ["gpu", "uuid"], registry=self.registry)
        self.gpu_device_error = Gauge("gpu_monitor_gpu_device_error", "GPU device error flag", ["gpu", "uuid"], registry=self.registry)
        self.gpu_dcgm_metric = Gauge("gpu_monitor_gpu_dcgm_metric", "Best-effort DCGM extra metric", ["gpu", "uuid", "field"], registry=self.registry)
        self.advanced_gpu_metrics = {
            field.key: Gauge(field.metric_name, field.description, ["gpu", "uuid"], registry=self.registry)
            for field in ADVANCED_TELEMETRY_FIELDS
        }

    def update_gpu_metrics(self, sample: dict[str, Any]) -> None:
        for gpu in sample.get("gpus", []):
            gpu_label = str(gpu.get("index"))
            uuid = str(gpu.get("uuid") or "")
            values = [
                (self.gpu_utilization, gpu.get("utilization_gpu")),
                (self.gpu_memory_used, gpu.get("memory_used_mb")),
                (self.gpu_memory_total, gpu.get("memory_total_mb")),
                (self.gpu_power_draw, gpu.get("power_draw_w")),
                (self.gpu_temperature, gpu.get("temperature_c")),
            ]
            for gauge, value in values:
                if value is not None:
                    gauge.labels(gpu=gpu_label, uuid=uuid).set(float(value))
            self.gpu_device_error.labels(gpu=gpu_label, uuid=uuid).set(1 if gpu.get("device_error") else 0)
            dcgm_values = gpu.get("dcgm") or {}
            for field_name, value in dcgm_values.items():
                if isinstance(value, (int, float)):
                    self.gpu_dcgm_metric.labels(gpu=gpu_label, uuid=uuid, field=str(field_name)).set(float(value))
            for field_name, value in normalize_advanced_telemetry(dcgm_values).items():
                self.advanced_gpu_metrics[field_name].labels(gpu=gpu_label, uuid=uuid).set(value)

    def render(self) -> bytes:
        return generate_latest(self.registry)


def _parse_history_timestamp(value: str) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _history_metric_values(point: dict[str, Any], metric: str, gpu: int | None = None) -> list[float]:
    values: list[float] = []
    for item in point.get("gpus", []):
        if gpu is not None and item.get("index") != gpu:
            continue
        value = item.get(metric)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def aggregate_history_points(
    points: list[dict[str, Any]],
    *,
    metric: str,
    bucket_seconds: int,
    agg: str,
    gpu: int | None = None,
) -> list[dict[str, Any]]:
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    aggregation = agg.lower()
    if aggregation not in {"avg", "min", "max", "count"}:
        raise ValueError("agg must be one of avg, min, max, count")
    buckets: dict[int, list[float]] = {}
    for point in points:
        timestamp = _parse_history_timestamp(str(point.get("timestamp") or ""))
        if timestamp is None:
            continue
        values = _history_metric_values(point, metric, gpu=gpu)
        if not values:
            continue
        bucket_start = int(timestamp // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_start, []).extend(values)
    result: list[dict[str, Any]] = []
    for bucket_start in sorted(buckets):
        values = buckets[bucket_start]
        if aggregation == "count":
            value = float(len(values))
        elif aggregation == "min":
            value = min(values)
        elif aggregation == "max":
            value = max(values)
        else:
            value = sum(values) / len(values)
        result.append(
            {
                "bucket_start": datetime.fromtimestamp(bucket_start, tz=timezone.utc).isoformat(),
                "bucket_seconds": bucket_seconds,
                "metric": metric,
                "agg": aggregation,
                "value": value,
                "count": len(values),
            }
        )
    return result


class MonitorRuntimeService:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.command_queue: queue.Queue[RuntimeCommand] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.collector = GPUCollector()
        self.notification_service = build_notification_service(self.config)
        self.metrics = RuntimeMetrics()
        self.state = MonitorState(start_ts=time.time())
        self.latest_sample: dict[str, Any] = {"timestamp": None, "gpus": [], "gpu_count": 0, "gpu_ids": []}
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self.history: deque[dict[str, Any]] = deque(maxlen=self.config.history.max_points_memory)
        self._history_path = self._resolve_path(self.config.history.path)
        self._last_history_persist_at = 0.0
        self.history_persist_error = ""
        self.event_persist_error = ""
        self.last_state_name = "INIT"
        self.last_reason = "not started"
        self.last_error = ""
        self.last_error_type = ""
        self.last_error_ts: str | None = None
        self.last_sample_ts: float | None = None
        self.consecutive_failures = 0
        self.notify_enabled = self.config.notify.control.enabled
        self.low_usage_notify_enabled = self.config.notify.control.low_usage_enabled
        self.silenced_alerts_until: dict[str, float] = {}
        self.silenced_alerts_permanent: set[str] = set()
        self.acknowledged_alerts: set[str] = set()
        self._event_log_path = self._resolve_path(self.config.logging.event_log_path)
        self._load_history()
        self.metrics.up.set(0)
        self.metrics.notify_enabled.set(1 if self.notify_enabled else 0)
        self._append_event("system", "runtime initialized", {"config": self.config.redacted_summary()})

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.config_path.parent / path

    def _set_state_metrics(self, state_name: str) -> None:
        mapping = {
            "INIT": 0,
            "WARMUP": 1,
            "WAITING_ACTIVE": 2,
            "ARMING": 3,
            "ACTIVE": 4,
            "LOW_USAGE_ALERT": 5,
            "NO_PROCESS_ALERT": 6,
            "ERROR": 7,
        }
        self.metrics.state_code.set(mapping.get(state_name, -1))
        self.metrics.state_info.info({"name": state_name})

    def _append_event(self, kind: str, message: str, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": kind,
            "message": message,
        }
        if extra:
            payload["extra"] = extra
        with self.lock:
            self.events.appendleft(payload)
        self._persist_event(payload)


    def _load_history(self) -> None:
        if not self.config.history.enabled or not self._history_path.exists():
            return
        try:
            lines = self._history_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            LOGGER.exception("Failed to load history", extra={"event_type": "history_load_error"})
            return
        for line in lines[-self.config.history.max_points_memory:]:
            if not line.strip():
                continue
            try:
                self.history.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _rotate_history_if_needed(self) -> None:
        if self.config.history.rotate_keep_files <= 0:
            return
        max_bytes = int(self.config.history.max_file_mb * 1024 * 1024)
        if not self._history_path.exists() or self._history_path.stat().st_size <= max_bytes:
            self._prune_history_by_retention()
            return
        oldest = self._history_path.with_suffix(self._history_path.suffix + f".{self.config.history.rotate_keep_files}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.config.history.rotate_keep_files - 1, 0, -1):
            source = self._history_path.with_suffix(self._history_path.suffix + f".{index}")
            if source.exists():
                source.replace(self._history_path.with_suffix(self._history_path.suffix + f".{index + 1}"))
        self._history_path.replace(self._history_path.with_suffix(self._history_path.suffix + ".1"))
        self._prune_history_by_retention()

    def _prune_history_by_retention(self) -> None:
        retention_days = self.config.history.retention_days
        if retention_days <= 0:
            return
        cutoff = time.time() - retention_days * 86400
        for path in [self._history_path, *self._history_path.parent.glob(self._history_path.name + ".*")]:
            try:
                if path.exists() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                LOGGER.warning("Failed to prune old history file", extra={"event_type": "history_prune_error", "path": str(path)})

    def _persist_history_point(self, point: dict[str, Any], now: float) -> None:
        if not self.config.history.enabled:
            return
        if now - self._last_history_persist_at < self.config.history.persist_interval_seconds:
            return
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_history_if_needed()
            with self._history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(point, ensure_ascii=False) + "\n")
            self._last_history_persist_at = now
            self.history_persist_error = ""
        except OSError as exc:
            self.history_persist_error = str(exc)
            self._append_event("history_persist_error", str(exc))

    def _append_history(self, sample: dict[str, Any], state_name: str, reason: str) -> None:
        point = {
            "timestamp": sample.get("timestamp"),
            "state": state_name,
            "reason": reason,
            "gpus": [
                {
                    "index": gpu.get("index"),
                    "utilization_gpu": gpu.get("utilization_gpu"),
                    "memory_used_mb": gpu.get("memory_used_mb"),
                    "power_draw_w": gpu.get("power_draw_w"),
                    "temperature_c": gpu.get("temperature_c"),
                    "device_error": gpu.get("device_error"),
                }
                for gpu in sample.get("gpus", [])
            ],
            "collector_errors": sample.get("collector_errors", {}),
        }
        with self.lock:
            self.history.append(point)
        self._persist_history_point(point, time.time())

    def get_history(self, limit: int | None = None, since: str = "", gpu: int | None = None, state: str = "", bucket_seconds: int | None = None, metric: str = "utilization_gpu", agg: str = "avg") -> dict[str, Any]:
        requested_limit = limit if limit is not None else self.config.history.query_default_limit
        safe_limit = max(1, min(int(requested_limit), self.history.maxlen or self.config.history.max_points_memory))
        since_ts = _parse_history_timestamp(since) if since else None
        state_filter = state.strip().upper()
        with self.lock:
            points = list(self.history)
            events = list(self.events)[-safe_limit:]
        if since_ts is not None:
            points = [point for point in points if (_parse_history_timestamp(str(point.get("timestamp") or "")) or 0) >= since_ts]
        if state_filter:
            points = [point for point in points if str(point.get("state") or "").upper() == state_filter]
        if gpu is not None:
            filtered_points = []
            for point in points:
                gpus = [item for item in point.get("gpus", []) if item.get("index") == gpu]
                if gpus:
                    copy = dict(point)
                    copy["gpus"] = gpus
                    filtered_points.append(copy)
            points = filtered_points
        result_points = points[-safe_limit:]
        result: dict[str, Any] = {"ok": True, "points": result_points, "events": events}
        if bucket_seconds is not None:
            result["aggregate"] = aggregate_history_points(result_points, metric=metric, bucket_seconds=bucket_seconds, agg=agg, gpu=gpu)
        return result

    def get_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "version": __version__,
                "monitor_state": self.last_state_name,
                "reason": self.last_reason,
                "last_error": self.last_error,
                "last_error_type": self.last_error_type,
                "notify_enabled": self.notify_enabled,
                "low_usage_notify_enabled": self.low_usage_notify_enabled,
                "notifier_order_active": self.notification_service.active_channels,
                "notifier_health": self.notification_service.get_channel_health(),
                "silenced_alerts_until": dict(self.silenced_alerts_until),
                "silenced_alerts_permanent": sorted(self.silenced_alerts_permanent),
                "acknowledged_alerts": sorted(self.acknowledged_alerts),
                "config_path": str(self.config_path),
                "config_summary": self.config.redacted_summary(),
                "sample": self.latest_sample,
                "events": list(self.events),
                "interval_seconds": self.config.monitor.interval_seconds,
                "cooldown_minutes": self.config.alert.cooldown_minutes,
                "min_interval_minutes": self.config.alert.min_interval_minutes,
                "muted_gpu_error_ids": self.config.alert.gpu_error.muted_gpu_ids,
                "platform_summary": self.latest_sample.get("platform_summary", {}),
            }

    def _persist_event(self, payload: dict[str, Any]) -> None:
        if not self.config.logging.event_log_path:
            return
        try:
            self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._event_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.event_persist_error = str(exc)
            LOGGER.exception("Failed to persist runtime event", extra={"event_type": "event_persist_error"})

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._loop, name="gpu-monitor-runtime", daemon=True)
        self.worker.start()
        self.metrics.up.set(1)
        self._append_event("system", "runtime started")

    def stop(self) -> None:
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=max(5, self.config.monitor.command_timeout_seconds + 2))
        self.metrics.up.set(0)
        self._append_event("system", "runtime stopped")

    def _submit_command(self, action: str, payload: dict[str, Any] | None = None, timeout_seconds: int = 15) -> Any:
        if self.worker is None or not self.worker.is_alive():
            response: queue.Queue[Any] = queue.Queue(maxsize=1)
            command = RuntimeCommand(action=action, payload=payload or {}, response=response)
            self._execute_command(command)
            result = response.get(timeout=1)
            if isinstance(result, Exception):
                raise result
            return result
        response: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.command_queue.put(RuntimeCommand(action=action, payload=payload or {}, response=response))
        result = response.get(timeout=timeout_seconds)
        if isinstance(result, Exception):
            raise result
        return result

    def set_notify_enabled(self, enabled: bool) -> bool:
        return bool(self._submit_command("set_notify_enabled", {"enabled": enabled}))

    def set_low_usage_notify_enabled(self, enabled: bool) -> bool:
        return bool(self._submit_command("set_low_usage_notify_enabled", {"enabled": enabled}))

    def set_gpu_error_muted(self, gpu_id: int, muted: bool) -> list[int]:
        return list(self._submit_command("set_gpu_error_muted", {"gpu_id": gpu_id, "muted": muted}))

    def reload_config(self) -> dict[str, Any]:
        return dict(self._submit_command("reload_config"))

    def acknowledge_alert(self, alert_key: str, silence_minutes: float = 0) -> dict[str, Any]:
        return dict(self._submit_command("acknowledge_alert", {"alert_key": alert_key, "silence_minutes": silence_minutes}))

    def silence_alert(self, alert_key: str, mode: str) -> dict[str, Any]:
        return dict(self._submit_command("silence_alert", {"alert_key": alert_key, "mode": mode}))

    def send_test_notification(self, channel: str | None = None) -> bool:
        return bool(self._submit_command("send_test_notification", {"channel": channel}))

    def get_status(self) -> dict[str, Any]:
        return self.get_snapshot()

    def get_health(self) -> dict[str, Any]:
        with self.lock:
            runtime_thread_alive = self.worker is not None and self.worker.is_alive()
            collector_errors = dict(self.latest_sample.get("collector_errors") or {})
            notifier_health = self.notification_service.get_channel_health()
            degraded_domains: list[str] = []
            if collector_errors:
                degraded_domains.append("collector")
            if any(channel.get("status") == "error" for channel in notifier_health.values()):
                degraded_domains.append("notifier")
            if self.history_persist_error or self.event_persist_error:
                degraded_domains.append("history")
            if self.consecutive_failures:
                degraded_domains.append("runtime")
            return {
                "ok": runtime_thread_alive and not self.consecutive_failures,
                "ready": runtime_thread_alive,
                "version": __version__,
                "monitor_state": self.last_state_name,
                "last_sample_timestamp": self.latest_sample.get("timestamp"),
                "consecutive_failures": self.consecutive_failures,
                "last_error": self.last_error,
                "last_error_type": self.last_error_type,
                "last_error_timestamp": self.last_error_ts,
                "uptime_seconds": max(0.0, time.time() - self.started_at),
                "runtime_thread_alive": runtime_thread_alive,
                "degraded_domains": degraded_domains,
                "collector_errors": collector_errors,
                "notifier_health": notifier_health,
                "history_persist_error": self.history_persist_error,
                "event_persist_error": self.event_persist_error,
                "config_warnings": self.config.warnings(),
            }

    def get_metrics_payload(self) -> bytes:
        if not self.config.metrics.enabled:
            return b"# gpu_monitor_metrics disabled by config\n"
        return self.metrics.render()

    def is_authorized(self, authorization_header: str | None, write: bool) -> bool:
        auth = self.config.dashboard.auth
        if not auth.enabled:
            return True
        if not write and not auth.require_auth_for_read:
            return True
        if not authorization_header:
            return False
        expected = f"Bearer {auth.token}"
        return authorization_header.strip() == expected

    def _execute_command(self, command: RuntimeCommand) -> None:
        try:
            if command.action == "set_notify_enabled":
                enabled = bool(command.payload["enabled"])
                self.notify_enabled = enabled
                self.metrics.notify_enabled.set(1 if enabled else 0)
                self._append_event("control", f"notify enabled set to {enabled}")
                command.response.put(enabled)
                return

            if command.action == "set_low_usage_notify_enabled":
                enabled = bool(command.payload["enabled"])
                self.low_usage_notify_enabled = enabled
                self._append_event("control", f"low usage notify enabled set to {enabled}")
                command.response.put(enabled)
                return

            if command.action == "set_gpu_error_muted":
                gpu_id = int(command.payload["gpu_id"])
                muted = bool(command.payload.get("muted", True))
                current = set(self.config.alert.gpu_error.muted_gpu_ids)
                if muted:
                    current.add(gpu_id)
                else:
                    current.discard(gpu_id)
                object.__setattr__(self.config.alert.gpu_error, "muted_gpu_ids", sorted(current))
                self._append_event("control", f"gpu error alert muted={muted} gpu={gpu_id}")
                command.response.put(self.config.alert.gpu_error.muted_gpu_ids)
                return

            if command.action == "reload_config":
                new_config = load_config(self.config_path)
                old_enabled = self.notify_enabled
                old_low_usage_notify_enabled = self.low_usage_notify_enabled
                old_muted_gpu_error_ids = self.config.alert.gpu_error.muted_gpu_ids
                object.__setattr__(new_config.alert.gpu_error, "muted_gpu_ids", old_muted_gpu_error_ids)
                self.config = new_config
                self.notification_service = build_notification_service(new_config)
                self._event_log_path = self._resolve_path(self.config.logging.event_log_path)
                self._history_path = self._resolve_path(self.config.history.path)
                self.notify_enabled = old_enabled
                self.low_usage_notify_enabled = old_low_usage_notify_enabled
                self.metrics.notify_enabled.set(1 if self.notify_enabled else 0)
                LOGGER.info("config reloaded", extra={"event_type": "config_reloaded"})
                self._append_event("control", "config reloaded", {"config": self.config.redacted_summary()})
                command.response.put({"ok": True, "config_summary": self.config.redacted_summary()})
                return

            if command.action == "acknowledge_alert":
                alert_key = str(command.payload.get("alert_key") or self.state.active_alert or "")
                silence_minutes = float(command.payload.get("silence_minutes") or 0)
                if not alert_key:
                    command.response.put({"ok": False, "error": "alert_key is required"})
                    return
                if silence_minutes > 0:
                    self.silenced_alerts_until[alert_key] = time.time() + silence_minutes * 60
                else:
                    self.acknowledged_alerts.add(alert_key)
                self._append_event("alert_ack", f"acknowledged {alert_key}", {"silence_minutes": silence_minutes})
                command.response.put({"ok": True, "alert_key": alert_key, "silence_minutes": silence_minutes})
                return

            if command.action == "silence_alert":
                alert_key = str(command.payload.get("alert_key") or self.state.active_alert or "")
                mode = str(command.payload.get("mode") or "").strip().lower()
                if not alert_key:
                    command.response.put({"ok": False, "error": "alert_key is required"})
                    return
                if mode == "1h":
                    self.silenced_alerts_until[alert_key] = time.time() + 3600
                    self.silenced_alerts_permanent.discard(alert_key)
                elif mode == "today":
                    now_dt = datetime.now().astimezone()
                    end_dt = now_dt.replace(hour=23, minute=59, second=59, microsecond=0)
                    self.silenced_alerts_until[alert_key] = end_dt.timestamp()
                    self.silenced_alerts_permanent.discard(alert_key)
                elif mode == "permanent":
                    self.silenced_alerts_permanent.add(alert_key)
                    self.silenced_alerts_until.pop(alert_key, None)
                elif mode == "clear":
                    self.silenced_alerts_until.pop(alert_key, None)
                    self.silenced_alerts_permanent.discard(alert_key)
                    self.acknowledged_alerts.discard(alert_key)
                else:
                    command.response.put({"ok": False, "error": "mode must be one of 1h, today, permanent, clear"})
                    return
                self._append_event("alert_silence", f"{mode} {alert_key}", {"alert": alert_key, "mode": mode})
                command.response.put({"ok": True, "alert_key": alert_key, "mode": mode, "silenced_alerts_permanent": sorted(self.silenced_alerts_permanent), "silenced_alerts_until": dict(self.silenced_alerts_until)})
                return

            if command.action == "send_test_notification":
                instance_name = self.config.monitor.instance_name
                subject = f"[GPU Monitor][{instance_name}] TEST"
                body = (
                    f"monitor: {instance_name}\n"
                    f"time(utc): {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                    "message: test notification from runtime"
                )
                if not self.notify_enabled:
                    self._append_event("notify_skip", "notify disabled by master switch", {"subject": subject})
                    command.response.put(False)
                    return
                result = self.notification_service.send(subject, body, command.payload.get("channel"))
                self.metrics.notify_total.labels(notifier=result.notifier, outcome="success").inc()
                self._append_event("notify_sent", subject, {"notifier": result.notifier, "channel": command.payload.get("channel")})
                command.response.put(True)
                return

            command.response.put(RuntimeError(f"Unknown runtime command: {command.action}"))
        except Exception as exc:  # noqa: BLE001
            self._append_event("command_error", f"{command.action} failed: {exc}")
            command.response.put(exc)

    def _maybe_send_alert(self, evaluation: EvaluationResult, sample: dict[str, Any], now: float) -> None:
        cooldown_seconds = self.config.alert.cooldown_minutes * 60
        min_interval_seconds = self.config.alert.min_interval_minutes * 60
        if not evaluation.alert_key:
            return
        self.state.alert_first_seen_at.setdefault(evaluation.alert_key, now)
        silenced_until = self.silenced_alerts_until.get(evaluation.alert_key)
        if silenced_until and silenced_until > now:
            self._append_event("notify_skip", "alert temporarily silenced", {"alert": evaluation.alert_key})
            self.state.active_alert = evaluation.alert_key
            return
        if silenced_until and silenced_until <= now:
            self.silenced_alerts_until.pop(evaluation.alert_key, None)
        if evaluation.alert_key in self.acknowledged_alerts:
            self._append_event("notify_skip", "alert acknowledged", {"alert": evaluation.alert_key})
            self.state.active_alert = evaluation.alert_key
            return
        if not self.notify_enabled:
            self._append_event("notify_skip", "notify disabled by master switch", {"alert": evaluation.alert_key})
            self.state.active_alert = evaluation.alert_key
            return
        if evaluation.alert_key in self.silenced_alerts_permanent:
            self._append_event("notify_skip", "alert permanently silenced", {"alert": evaluation.alert_key})
            self.state.active_alert = evaluation.alert_key
            return
        if evaluation.alert_key == "LOW_USAGE_ALERT" and not self.low_usage_notify_enabled:
            self._append_event("notify_skip", "low usage notify disabled", {"alert": evaluation.alert_key})
            return
        if not should_send_with_cooldown(self.state, evaluation.alert_key, cooldown_seconds, now):
            self.state.active_alert = evaluation.alert_key
            return
        if not should_send_by_global_interval(self.state, min_interval_seconds, now):
            self.state.active_alert = evaluation.alert_key
            return
        subject, body = build_alert_message(
            self.config.monitor.instance_name,
            evaluation.alert_key,
            sample,
            evaluation.reason,
            self.config.scenario_profile,
            {
                "low_usage_hint": self.config.scenario_messages.low_usage_hint,
                "no_process_hint": self.config.scenario_messages.no_process_hint,
            },
        )
        subject, body = apply_message_template(
            self.config,
            evaluation.alert_key,
            subject,
            body,
            {"instance": self.config.monitor.instance_name, "alert": evaluation.alert_key, "reason": evaluation.reason},
        )
        try:
            result = self.notification_service.send(subject, body)
        except Exception as notify_exc:  # noqa: BLE001
            self.metrics.notify_total.labels(notifier="alert", outcome="error").inc()
            self._append_event("notify_error", str(notify_exc), {"alert": evaluation.alert_key})
            LOGGER.warning("Alert notification failed", extra={"event_type": "notify_error", "alert": evaluation.alert_key})
            self.state.active_alert = evaluation.alert_key
            return
        mark_alert_sent(self.state, evaluation.alert_key, now)
        self.metrics.alert_total.labels(alert_key=evaluation.alert_key).inc()
        self.metrics.notify_total.labels(notifier=result.notifier, outcome="success").inc()
        self._append_event("notify_sent", subject, {"notifier": result.notifier, "alert": evaluation.alert_key})
        escalation = find_escalation(self.config, self.state, evaluation.alert_key, now)
        if escalation is not None:
            try:
                escalated = self.notification_service.send(subject, body, channel_name=escalation.rule.channel)
                mark_alert_sent(self.state, escalation.key, now)
                self.metrics.notify_total.labels(notifier=escalated.notifier, outcome="success").inc()
                self._append_event("notify_sent", subject, {"notifier": escalated.notifier, "alert": evaluation.alert_key, "escalated": True})
            except Exception as notify_exc:  # noqa: BLE001
                self.metrics.notify_total.labels(notifier="escalation", outcome="error").inc()
                self._append_event("notify_error", str(notify_exc), {"alert": evaluation.alert_key, "escalated": True})
        self.state.active_alert = evaluation.alert_key

    def _maybe_send_runtime_error_alert(self, now: float) -> None:
        runtime_error = self.config.alert.runtime_error
        alert_key = "RUNTIME_ERROR_ALERT"
        if not runtime_error.enabled:
            return
        if self.consecutive_failures < runtime_error.consecutive_failures:
            return
        self.state.alert_first_seen_at.setdefault(alert_key, now)
        if not self.notify_enabled:
            self._append_event("notify_skip", "notify disabled by master switch", {"alert": alert_key})
            self.state.active_alert = alert_key
            return
        cooldown_seconds = runtime_error.cooldown_minutes * 60
        min_interval_seconds = self.config.alert.min_interval_minutes * 60
        if not should_send_with_cooldown(self.state, alert_key, cooldown_seconds, now):
            self.state.active_alert = alert_key
            return
        if not should_send_by_global_interval(self.state, min_interval_seconds, now):
            self.state.active_alert = alert_key
            return
        subject = f"[GPU Monitor][{self.config.monitor.instance_name}] {alert_key}"
        body = "\n".join(
            [
                f"monitor: {self.config.monitor.instance_name}",
                f"time(utc): {self.last_error_ts}",
                f"alert: {alert_key}",
                f"reason: runtime cycle failed",
                f"error_type: {self.last_error_type}",
                f"consecutive_failures: {self.consecutive_failures}",
                f"last_error: {self.last_error}",
            ]
        )
        subject, body = apply_message_template(
            self.config,
            alert_key,
            subject,
            body,
            {"instance": self.config.monitor.instance_name, "alert": alert_key, "reason": "runtime cycle failed"},
        )
        try:
            result = self.notification_service.send(subject, body)
        except Exception as notify_exc:  # noqa: BLE001
            self.metrics.notify_total.labels(notifier="runtime_error_alert", outcome="error").inc()
            self._append_event("notify_error", str(notify_exc), {"alert": alert_key})
            LOGGER.warning("Runtime error alert notification failed", extra={"event_type": "notify_error", "alert": alert_key})
            self.state.active_alert = alert_key
            return
        mark_alert_sent(self.state, alert_key, now)
        self.metrics.alert_total.labels(alert_key=alert_key).inc()
        self.metrics.notify_total.labels(notifier=result.notifier, outcome="success").inc()
        self._append_event("notify_sent", subject, {"notifier": result.notifier, "alert": alert_key})
        escalation = find_escalation(self.config, self.state, alert_key, now)
        if escalation is not None:
            try:
                escalated = self.notification_service.send(subject, body, channel_name=escalation.rule.channel)
                mark_alert_sent(self.state, escalation.key, now)
                self.metrics.notify_total.labels(notifier=escalated.notifier, outcome="success").inc()
                self._append_event("notify_sent", subject, {"notifier": escalated.notifier, "alert": alert_key, "escalated": True})
            except Exception as notify_exc:  # noqa: BLE001
                self.metrics.notify_total.labels(notifier="escalation", outcome="error").inc()
                self._append_event("notify_error", str(notify_exc), {"alert": alert_key, "escalated": True})
        self.state.active_alert = alert_key
    def _maybe_send_recovery(self, sample: dict[str, Any], now: float) -> None:
        previous_alert = self.state.active_alert
        if not previous_alert or not can_send_recovery(self.config, previous_alert):
            self.state.active_alert = None
            return
        recovery_key = recovery_alert_key(previous_alert)
        cooldown_seconds = self.config.alert.recovery.cooldown_minutes * 60
        min_interval_seconds = self.config.alert.min_interval_minutes * 60
        if not self.notify_enabled:
            self.state.active_alert = None
            return
        if not should_send_with_cooldown(self.state, recovery_key, cooldown_seconds, now):
            self.state.active_alert = None
            return
        if not should_send_by_global_interval(self.state, min_interval_seconds, now):
            self.state.active_alert = None
            return
        subject, body = build_recovered_message(self.config.monitor.instance_name, previous_alert, sample)
        subject, body = apply_message_template(
            self.config,
            recovery_key,
            subject,
            body,
            {"instance": self.config.monitor.instance_name, "alert": recovery_key, "recovered_from": previous_alert},
        )
        try:
            result = self.notification_service.send(subject, body)
        except Exception as notify_exc:  # noqa: BLE001
            self.metrics.notify_total.labels(notifier="recovery", outcome="error").inc()
            self._append_event("notify_error", str(notify_exc), {"recovered_from": previous_alert})
            LOGGER.warning("Recovery notification failed", extra={"event_type": "notify_error", "recovered_from": previous_alert})
            self.state.active_alert = previous_alert
            return
        mark_alert_sent(self.state, recovery_key, now)
        self.metrics.notify_total.labels(notifier=result.notifier, outcome="success").inc()
        self._append_event("notify_sent", subject, {"notifier": result.notifier, "recovered_from": previous_alert})
        self.acknowledged_alerts.discard(previous_alert)
        self.silenced_alerts_until.pop(previous_alert, None)
        self.silenced_alerts_permanent.discard(previous_alert)
        self.state.alert_first_seen_at.pop(previous_alert, None)
        self.state.active_alert = None

    def _run_cycle(self) -> None:
        now = time.time()
        sample = self.collector.collect_sample(self.config.monitor, self.config.platform)
        evaluation = evaluate_state(self.config, self.state, sample, now=now)

        previous_state_name = self.last_state_name
        self.latest_sample = sample
        self._append_history(sample, evaluation.state_name, evaluation.reason)
        self.last_state_name = evaluation.state_name
        self.last_reason = evaluation.reason
        self.last_error = ""
        self.last_error_type = ""
        self.last_error_ts = None
        self.last_sample_ts = now
        self.consecutive_failures = 0
        self.metrics.last_sample_ts.set(now)
        self.metrics.consecutive_failures.set(0)
        self.metrics.notify_enabled.set(1 if self.notify_enabled else 0)
        self.metrics.update_gpu_metrics(sample)
        self.metrics.history_points.set(len(self.history))
        self.metrics.history_events.set(len(self.events))
        self.metrics.acknowledged_alerts.set(len(self.acknowledged_alerts))
        self.metrics.silenced_alerts.labels(mode="temporary").set(len(self.silenced_alerts_until))
        self.metrics.silenced_alerts.labels(mode="permanent").set(len(self.silenced_alerts_permanent))
        for notifier_name, health in self.notification_service.get_channel_health().items():
            self.metrics.notifier_health.labels(notifier=str(notifier_name)).set(1 if health.get("status") == "ok" else 0)
        self._set_state_metrics(evaluation.state_name)

        LOGGER.info(
            "monitor cycle",
            extra={
                "event_type": "monitor_cycle",
                "state": evaluation.state_name,
                "reason": evaluation.reason,
                "gpu_count": sample.get("gpu_count", 0),
            },
        )

        if evaluation.state_name != previous_state_name:
            self._append_event("state_transition", f"{previous_state_name} -> {evaluation.state_name}", {"from": previous_state_name, "to": evaluation.state_name, "reason": evaluation.reason})
        if evaluation.alert_key:
            self._maybe_send_alert(evaluation, sample, now)
        elif self.state.active_alert is not None and evaluation.state_name == "ACTIVE":
            self._maybe_send_recovery(sample, now)

    def _handle_cycle_error(self, exc: Exception) -> None:
        self.consecutive_failures += 1
        self.last_state_name = "ERROR"
        self.last_reason = "runtime cycle failed"
        self.last_error = str(exc)
        self.last_error_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if isinstance(exc, CollectorError):
            self.last_error_type = exc.error_type
        elif isinstance(exc, NotificationError):
            self.last_error_type = "notification_error"
        else:
            self.last_error_type = exc.__class__.__name__.lower()
        self.metrics.collection_errors_total.labels(error_type=self.last_error_type).inc()
        self.metrics.consecutive_failures.set(self.consecutive_failures)
        self._set_state_metrics("ERROR")
        self._append_event("error", self.last_error, {"error_type": self.last_error_type, "consecutive_failures": self.consecutive_failures})
        self._maybe_send_runtime_error_alert(time.time())
        LOGGER.exception(
            "runtime cycle error",
            extra={"event_type": "runtime_error", "error_type": self.last_error_type, "state": "ERROR"},
        )

    def _loop(self) -> None:
        next_sample_at = 0.0
        while not self.stop_event.is_set():
            timeout = max(0.0, next_sample_at - time.monotonic())
            try:
                command = self.command_queue.get(timeout=timeout)
                self._execute_command(command)
                continue
            except queue.Empty:
                pass

            try:
                self._run_cycle()
            except Exception as exc:  # noqa: BLE001
                self._handle_cycle_error(exc)
            next_sample_at = time.monotonic() + self.config.monitor.interval_seconds


