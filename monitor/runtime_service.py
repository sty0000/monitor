from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Info, generate_latest

from .collector import CollectorError, GPUCollector
from .config import AppConfig, load_config
from .notification_service import NotificationError, build_notification_service
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
        self.alert_total = Counter("gpu_monitor_alert_total", "Alerts emitted", ["alert_key"], registry=self.registry)
        self.notify_total = Counter("gpu_monitor_notify_total", "Notification attempts", ["notifier", "outcome"], registry=self.registry)
        self.collection_errors_total = Counter("gpu_monitor_collection_errors_total", "Collection errors", ["error_type"], registry=self.registry)

    def render(self) -> bytes:
        return generate_latest(self.registry)


class MonitorRuntimeService:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
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
        self.last_state_name = "INIT"
        self.last_reason = "not started"
        self.last_error = ""
        self.last_error_type = ""
        self.last_error_ts: str | None = None
        self.last_sample_ts: float | None = None
        self.consecutive_failures = 0
        self.notify_enabled = self.config.notify.control.enabled
        self._event_log_path = self._resolve_path(self.config.logging.event_log_path)
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

    def get_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "monitor_state": self.last_state_name,
                "reason": self.last_reason,
                "last_error": self.last_error,
                "last_error_type": self.last_error_type,
                "notify_enabled": self.notify_enabled,
                "notifier_order_active": self.notification_service.active_channels,
                "config_path": str(self.config_path),
                "config_summary": self.config.redacted_summary(),
                "sample": self.latest_sample,
                "events": list(self.events),
                "interval_seconds": self.config.monitor.interval_seconds,
                "cooldown_minutes": self.config.alert.cooldown_minutes,
                "min_interval_minutes": self.config.alert.min_interval_minutes,
            }

    def _persist_event(self, payload: dict[str, Any]) -> None:
        if not self.config.logging.event_log_path:
            return
        try:
            self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._event_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
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

    def reload_config(self) -> dict[str, Any]:
        return dict(self._submit_command("reload_config"))

    def send_test_notification(self, channel: str | None = None) -> bool:
        return bool(self._submit_command("send_test_notification", {"channel": channel}))

    def get_status(self) -> dict[str, Any]:
        return self.get_snapshot()

    def get_health(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": self.worker is not None and self.worker.is_alive(),
                "monitor_state": self.last_state_name,
                "last_sample_timestamp": self.latest_sample.get("timestamp"),
                "consecutive_failures": self.consecutive_failures,
                "last_error": self.last_error,
                "last_error_type": self.last_error_type,
                "last_error_timestamp": self.last_error_ts,
            }

    def get_metrics_payload(self) -> bytes:
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

            if command.action == "reload_config":
                new_config = load_config(self.config_path)
                old_enabled = self.notify_enabled
                self.config = new_config
                self.notification_service = build_notification_service(new_config)
                self._event_log_path = self._resolve_path(self.config.logging.event_log_path)
                self.notify_enabled = old_enabled
                self.metrics.notify_enabled.set(1 if self.notify_enabled else 0)
                LOGGER.info("config reloaded", extra={"event_type": "config_reloaded"})
                self._append_event("control", "config reloaded", {"config": self.config.redacted_summary()})
                command.response.put({"ok": True, "config_summary": self.config.redacted_summary()})
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
        if not self.notify_enabled:
            self._append_event("notify_skip", "notify disabled by master switch", {"alert": evaluation.alert_key})
            self.state.active_alert = evaluation.alert_key
            return
        if not should_send_with_cooldown(self.state, evaluation.alert_key, cooldown_seconds, now):
            self.state.active_alert = evaluation.alert_key
            return
        if not should_send_by_global_interval(self.state, min_interval_seconds, now):
            self.state.active_alert = evaluation.alert_key
            return
        subject, body = build_alert_message(self.config.monitor.instance_name, evaluation.alert_key, sample, evaluation.reason)
        result = self.notification_service.send(subject, body)
        mark_alert_sent(self.state, evaluation.alert_key, now)
        self.metrics.alert_total.labels(alert_key=evaluation.alert_key).inc()
        self.metrics.notify_total.labels(notifier=result.notifier, outcome="success").inc()
        self._append_event("notify_sent", subject, {"notifier": result.notifier, "alert": evaluation.alert_key})
        self.state.active_alert = evaluation.alert_key

    def _maybe_send_recovery(self, sample: dict[str, Any], now: float) -> None:
        previous_alert = self.state.active_alert
        if not previous_alert or not can_send_recovery(self.config, previous_alert):
            self.state.active_alert = None
            return
        recovery_key = f"RECOVERED:{previous_alert}"
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
        result = self.notification_service.send(subject, body)
        mark_alert_sent(self.state, recovery_key, now)
        self.metrics.notify_total.labels(notifier=result.notifier, outcome="success").inc()
        self._append_event("notify_sent", subject, {"notifier": result.notifier, "recovered_from": previous_alert})
        self.state.active_alert = None

    def _run_cycle(self) -> None:
        now = time.time()
        sample = self.collector.collect_sample(self.config.monitor)
        evaluation = evaluate_state(self.config, self.state, sample, now=now)

        self.latest_sample = sample
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
