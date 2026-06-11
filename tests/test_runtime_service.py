from pathlib import Path

from monitor.runtime_service import MonitorRuntimeService


class CapturingNotificationService:
    active_channels = ["test"]

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.channels: list[str | None] = []

    def send(self, subject: str, body: str, channel_name: str | None = None):
        self.messages.append((subject, body))
        self.channels.append(channel_name)
        return type("Result", (), {"notifier": "test", "attempted": ["test"]})()


def _write_config(path: Path) -> None:
    path.write_text(
        """
monitor:
  instance_name: "server-a"
alert:
  min_interval_minutes: 0
  runtime_error:
    enabled: true
    consecutive_failures: 2
    cooldown_minutes: 30
dashboard:
  auth:
    enabled: false
notify:
  control:
    enabled: true
""",
        encoding="utf-8",
    )


def test_runtime_error_alert_after_consecutive_failures(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier

    runtime._handle_cycle_error(ValueError("boom-1"))
    assert notifier.messages == []

    runtime._handle_cycle_error(ValueError("boom-2"))
    assert len(notifier.messages) == 1
    subject, body = notifier.messages[0]
    assert "RUNTIME_ERROR_ALERT" in subject
    assert "consecutive_failures: 2" in body
    assert "last_error: boom-2" in body

    runtime._handle_cycle_error(ValueError("boom-3"))
    assert len(notifier.messages) == 1


def test_low_usage_notify_switch_skips_only_low_usage_alert(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier
    runtime.low_usage_notify_enabled = False

    evaluation = type("Evaluation", (), {"alert_key": "LOW_USAGE_ALERT"})()
    runtime._maybe_send_alert(evaluation, {"timestamp": "t", "gpus": []}, 1000)

    assert notifier.messages == []
    assert runtime.state.active_alert is None



def test_runtime_history_records_compact_samples(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    sample = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "gpus": [{"index": 0, "utilization_gpu": 50, "memory_used_mb": 1000, "power_draw_w": 200, "temperature_c": 70, "device_error": None}],
        "collector_errors": {"dcgm": "fallback"},
    }

    runtime._append_history(sample, "ACTIVE", "ok")
    history = runtime.get_history(limit=10)

    assert history["ok"] is True
    assert history["points"][0]["state"] == "ACTIVE"
    assert history["points"][0]["gpus"][0]["utilization_gpu"] == 50
    assert history["points"][0]["collector_errors"] == {"dcgm": "fallback"}



def test_acknowledge_alert_suppresses_repeated_alert(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier

    result = runtime.acknowledge_alert("LOW_USAGE_ALERT", silence_minutes=0)
    assert result["ok"] is True
    evaluation = type("Evaluation", (), {"alert_key": "LOW_USAGE_ALERT", "reason": "idle"})()
    runtime._maybe_send_alert(evaluation, {"timestamp": "t", "gpus": []}, 1000)

    assert notifier.messages == []
    assert "LOW_USAGE_ALERT" in runtime.acknowledged_alerts



def test_runtime_metrics_include_per_gpu_values() -> None:
    from monitor.runtime_service import RuntimeMetrics

    metrics = RuntimeMetrics()
    metrics.update_gpu_metrics({"gpus": [{"index": 0, "uuid": "GPU-0", "utilization_gpu": 42, "memory_used_mb": 100, "power_draw_w": 50, "temperature_c": 60, "device_error": "ERR!"}]})
    payload = metrics.render().decode("utf-8")

    assert "gpu_monitor_gpu_utilization_percent" in payload
    assert "gpu_monitor_gpu_device_error" in payload
    assert 'gpu="0"' in payload



def test_temporary_silence_expires_without_permanent_ack(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier

    runtime.acknowledge_alert("LOW_USAGE_ALERT", silence_minutes=1)
    assert "LOW_USAGE_ALERT" not in runtime.acknowledged_alerts
    runtime.silenced_alerts_until["LOW_USAGE_ALERT"] = 1001
    evaluation = type("Evaluation", (), {"alert_key": "LOW_USAGE_ALERT", "reason": "idle"})()
    runtime._maybe_send_alert(evaluation, {"timestamp": "t", "gpus": []}, 1000)
    assert notifier.messages == []

    runtime._maybe_send_alert(evaluation, {"timestamp": "t", "gpus": []}, 1002)
    assert len(notifier.messages) == 1



def test_runtime_history_persists_and_loads_jsonl(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: false
history:
  enabled: true
  path: logs/history.jsonl
  max_points_memory: 5
  max_file_mb: 1
  persist_interval_seconds: 1
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)
    sample = {"timestamp": "t", "gpus": [{"index": 0, "utilization_gpu": 1}], "collector_errors": {}}
    runtime._last_history_persist_at = 0
    runtime._append_history(sample, "ACTIVE", "ok")

    history_path = tmp_path / "logs" / "history.jsonl"
    assert history_path.exists()

    reloaded = MonitorRuntimeService(config_path)
    assert reloaded.get_history(limit=5)["points"][-1]["state"] == "ACTIVE"


def test_runtime_history_skips_bad_jsonl_lines(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    history_path = tmp_path / "logs" / "history.jsonl"
    history_path.parent.mkdir()
    history_path.write_text('{"ts":"ok","gpus":[]}\nnot-json\n', encoding="utf-8")
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: false
history:
  enabled: true
  path: logs/history.jsonl
""",
        encoding="utf-8",
    )

    runtime = MonitorRuntimeService(config_path)

    assert len(runtime.get_history()["points"]) == 1


def test_runtime_history_filters_by_state_and_gpu(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime._append_history({"timestamp": "2026-01-01T00:00:00+00:00", "gpus": [{"index": 0}, {"index": 1}]}, "ACTIVE", "ok")
    runtime._append_history({"timestamp": "2026-01-02T00:00:00+00:00", "gpus": [{"index": 1}]}, "ERROR", "bad")

    history = runtime.get_history(limit=10, since="2026-01-01T12:00:00+00:00", gpu=1, state="ERROR")

    assert len(history["points"]) == 1
    assert history["points"][0]["state"] == "ERROR"
    assert history["points"][0]["gpus"][0]["index"] == 1


def test_runtime_alert_silence_modes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime.state.active_alert = "GPU_ERROR_ALERT"

    result = runtime.silence_alert("", "permanent")
    assert result["ok"] is True
    assert "GPU_ERROR_ALERT" in runtime.silenced_alerts_permanent

    result = runtime.silence_alert("GPU_ERROR_ALERT", "clear")
    assert result["ok"] is True
    assert "GPU_ERROR_ALERT" not in runtime.silenced_alerts_permanent


def test_runtime_metrics_include_history_and_silence(tmp_path: Path) -> None:
    from monitor.runtime_service import RuntimeMetrics

    metrics = RuntimeMetrics()
    metrics.history_points.set(2)
    metrics.silenced_alerts.labels(mode="permanent").set(1)
    text = metrics.render().decode("utf-8")

    assert "gpu_monitor_history_points" in text
    assert "gpu_monitor_silenced_alerts" in text


class FailingNotificationService(CapturingNotificationService):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def send(self, subject: str, body: str, channel_name: str | None = None):
        if self.fail:
            raise RuntimeError("notify failed")
        return super().send(subject, body, channel_name)


def _evaluation(alert_key: str, reason: str = "reason"):
    return type("Evaluation", (), {"alert_key": alert_key, "reason": reason})()


def test_alert_cooldown_does_not_refresh_sent_timestamp(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier

    runtime._maybe_send_alert(_evaluation("LOW_USAGE_ALERT"), {"timestamp": "t", "gpus": []}, 1000)
    first_ts = runtime.state.last_alert_sent_at["LOW_USAGE_ALERT"]
    runtime._maybe_send_alert(_evaluation("LOW_USAGE_ALERT"), {"timestamp": "t", "gpus": []}, 1001)

    assert len(notifier.messages) == 1
    assert runtime.state.last_alert_sent_at["LOW_USAGE_ALERT"] == first_ts


def test_global_interval_does_not_mark_blocked_alert(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
monitor:
  instance_name: server-a
alert:
  cooldown_minutes: 0
  min_interval_minutes: 10
dashboard:
  auth:
    enabled: false
notify:
  control:
    enabled: true
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier

    runtime._maybe_send_alert(_evaluation("LOW_USAGE_ALERT"), {"timestamp": "t", "gpus": []}, 1000)
    runtime._maybe_send_alert(_evaluation("GPU_ERROR_ALERT"), {"timestamp": "t", "gpus": []}, 1001)

    assert len(notifier.messages) == 1
    assert "GPU_ERROR_ALERT" not in runtime.state.last_alert_sent_at
    assert runtime.state.last_any_alert_sent_at == 1000


def test_notifier_failure_does_not_mark_alert_and_next_success_retries(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = FailingNotificationService()
    runtime.notification_service = notifier

    try:
        runtime._maybe_send_alert(_evaluation("LOW_USAGE_ALERT"), {"timestamp": "t", "gpus": []}, 1000)
    except RuntimeError:
        pass
    assert "LOW_USAGE_ALERT" not in runtime.state.last_alert_sent_at

    notifier.fail = False
    runtime._maybe_send_alert(_evaluation("LOW_USAGE_ALERT"), {"timestamp": "t", "gpus": []}, 1001)

    assert len(notifier.messages) == 1
    assert runtime.state.last_alert_sent_at["LOW_USAGE_ALERT"] == 1001


def test_ack_and_silence_do_not_mark_and_clear_allows_send(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier

    runtime.acknowledge_alert("LOW_USAGE_ALERT")
    runtime._maybe_send_alert(_evaluation("LOW_USAGE_ALERT"), {"timestamp": "t", "gpus": []}, 1000)
    assert "LOW_USAGE_ALERT" not in runtime.state.last_alert_sent_at

    runtime.silence_alert("LOW_USAGE_ALERT", "clear")
    runtime._maybe_send_alert(_evaluation("LOW_USAGE_ALERT"), {"timestamp": "t", "gpus": []}, 1001)

    assert len(notifier.messages) == 1
    assert runtime.state.last_alert_sent_at["LOW_USAGE_ALERT"] == 1001


def test_runtime_error_alert_marks_only_after_success(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = FailingNotificationService()
    runtime.notification_service = notifier
    runtime.consecutive_failures = 1

    runtime._maybe_send_runtime_error_alert(1000)
    assert "RUNTIME_ERROR_ALERT" not in runtime.state.last_alert_sent_at

    runtime.consecutive_failures = 2
    runtime._maybe_send_runtime_error_alert(1001)
    assert "RUNTIME_ERROR_ALERT" not in runtime.state.last_alert_sent_at

    notifier.fail = False
    runtime._maybe_send_runtime_error_alert(1002)
    assert runtime.state.last_alert_sent_at["RUNTIME_ERROR_ALERT"] == 1002



def test_event_log_persists_jsonl_and_state_transition(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: false
logging:
  event_log_path: logs/events.jsonl
notify:
  control:
    enabled: false
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)
    runtime.last_state_name = "INIT"
    runtime.collector = type("Collector", (), {"collect_sample": lambda self, monitor, platform: {"timestamp": "t", "gpus": [], "gpu_count": 0, "collector_errors": {}}})()

    runtime._run_cycle()

    event_path = tmp_path / "logs" / "events.jsonl"
    lines = event_path.read_text(encoding="utf-8").splitlines()
    assert any('"kind": "state_transition"' in line for line in lines)
    assert all(line.startswith("{") for line in lines)


def test_event_persist_failure_degrades_health_without_raising(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    bad_parent = tmp_path / "not-a-dir"
    bad_parent.write_text("file", encoding="utf-8")
    config_path.write_text(
        f"""
dashboard:
  auth:
    enabled: false
logging:
  event_log_path: {bad_parent.as_posix()}/events.jsonl
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)

    runtime._append_event("unit", "message")

    health = runtime.get_health()
    assert health["event_persist_error"]
    assert "history" in health["degraded_domains"]


def test_recovery_notification_disabled_does_not_send(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: false
alert:
  recovery:
    enabled: false
notify:
  control:
    enabled: true
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier
    runtime.state.active_alert = "LOW_USAGE_ALERT"

    runtime._maybe_send_recovery({"timestamp": "t", "gpus": []}, 1000)

    assert notifier.messages == []
    assert "LOW_USAGE_RECOVERED" not in runtime.state.last_alert_sent_at


def test_recovery_notification_failure_does_not_mark_and_success_retries(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = FailingNotificationService()
    runtime.notification_service = notifier
    runtime.state.active_alert = "LOW_USAGE_ALERT"

    runtime._maybe_send_recovery({"timestamp": "t", "gpus": []}, 1000)
    assert "LOW_USAGE_RECOVERED" not in runtime.state.last_alert_sent_at
    assert runtime.state.active_alert == "LOW_USAGE_ALERT"

    notifier.fail = False
    runtime._maybe_send_recovery({"timestamp": "t", "gpus": []}, 1001)
    assert runtime.state.last_alert_sent_at["LOW_USAGE_RECOVERED"] == 1001
    assert runtime.state.active_alert is None


def test_recovery_notification_respects_cooldown(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: false
alert:
  min_interval_minutes: 0
  recovery:
    enabled: true
    cooldown_minutes: 10
notify:
  control:
    enabled: true
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier
    runtime.state.active_alert = "LOW_USAGE_ALERT"

    runtime._maybe_send_recovery({"timestamp": "t", "gpus": []}, 1000)
    runtime.state.active_alert = "LOW_USAGE_ALERT"
    runtime._maybe_send_recovery({"timestamp": "t", "gpus": []}, 1001)

    assert len(notifier.messages) == 1
    assert runtime.state.last_alert_sent_at["LOW_USAGE_RECOVERED"] == 1000


def test_alert_escalation_sends_to_configured_channel_after_delay(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
monitor:
  instance_name: server-a
dashboard:
  auth:
    enabled: false
alert:
  cooldown_minutes: 0
  min_interval_minutes: 0
notify:
  control:
    enabled: true
  escalation:
    enabled: true
    rules:
      - alert: HIGH_TEMPERATURE_ALERT
        after_minutes: 10
        channel: telegram
        cooldown_minutes: 30
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier
    runtime.state.alert_first_seen_at["HIGH_TEMPERATURE_ALERT"] = 100

    runtime._maybe_send_alert(_evaluation("HIGH_TEMPERATURE_ALERT"), {"timestamp": "t", "gpus": []}, 700)
    runtime._maybe_send_alert(_evaluation("HIGH_TEMPERATURE_ALERT"), {"timestamp": "t", "gpus": []}, 701)

    assert notifier.channels == [None, "telegram", None]
    assert runtime.state.last_alert_sent_at["ESCALATED:HIGH_TEMPERATURE_ALERT:telegram"] == 700


def test_recovery_notification_uses_specific_recovered_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    notifier = CapturingNotificationService()
    runtime.notification_service = notifier
    runtime.state.active_alert = "GPU_ERROR_ALERT"

    runtime._maybe_send_recovery({"timestamp": "t", "gpus": []}, 1000)

    assert runtime.state.last_alert_sent_at["GPU_ERROR_RECOVERED"] == 1000


def test_runtime_metrics_include_nvml_and_dcgm_extra_values() -> None:
    from monitor.runtime_service import RuntimeMetrics

    metrics = RuntimeMetrics()
    metrics.update_gpu_metrics({"gpus": [{"index": 0, "uuid": "GPU-0", "memory_total_mb": 8192, "dcgm": {"smclk": 1200}}]})
    payload = metrics.render().decode("utf-8")

    assert "gpu_monitor_gpu_memory_total_mb" in payload
    assert "gpu_monitor_gpu_dcgm_metric" in payload
    assert 'field="smclk"' in payload



def test_runtime_metrics_export_stable_advanced_telemetry_names() -> None:
    from monitor.runtime_service import RuntimeMetrics

    metrics = RuntimeMetrics()
    metrics.update_gpu_metrics(
        {
            "gpus": [
                {
                    "index": 0,
                    "uuid": "GPU-0",
                    "dcgm": {
                        "smclk": 1200,
                        "memclk": 5001,
                        "pcietx": 12.5,
                        "pcierx": 25.5,
                        "ecc": 2,
                        "xid": 0,
                        "ignored_text": "N/A",
                    },
                }
            ]
        }
    )
    payload = metrics.render().decode("utf-8")

    assert "gpu_monitor_gpu_sm_clock_mhz" in payload
    assert "gpu_monitor_gpu_mem_clock_mhz" in payload
    assert "gpu_monitor_gpu_pcie_tx_mb_s" in payload
    assert "gpu_monitor_gpu_pcie_rx_mb_s" in payload
    assert "gpu_monitor_gpu_ecc_error_count" in payload
    assert "gpu_monitor_gpu_xid_error_count" in payload
    assert 'field="smclk"' in payload
    assert "ignored_text" not in payload


def test_history_aggregation_avg_min_max_count(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    for timestamp, value in [
        ("2026-01-01T00:00:00+00:00", 10),
        ("2026-01-01T00:00:30+00:00", 30),
        ("2026-01-01T00:01:00+00:00", 50),
    ]:
        runtime._append_history({"timestamp": timestamp, "gpus": [{"index": 0, "utilization_gpu": value}]}, "ACTIVE", "ok")

    avg = runtime.get_history(limit=10, bucket_seconds=60, metric="utilization_gpu", agg="avg", gpu=0)["aggregate"]
    max_values = runtime.get_history(limit=10, bucket_seconds=60, metric="utilization_gpu", agg="max", gpu=0)["aggregate"]
    count_values = runtime.get_history(limit=10, bucket_seconds=60, metric="utilization_gpu", agg="count", gpu=0)["aggregate"]

    assert [item["value"] for item in avg] == [20, 50]
    assert [item["value"] for item in max_values] == [30, 50]
    assert [item["value"] for item in count_values] == [2, 1]


def test_history_aggregation_rejects_invalid_params(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime._append_history({"timestamp": "2026-01-01T00:00:00+00:00", "gpus": [{"index": 0, "utilization_gpu": 10}]}, "ACTIVE", "ok")

    try:
        runtime.get_history(limit=10, bucket_seconds=0, metric="utilization_gpu", agg="avg")
    except ValueError as exc:
        assert "bucket_seconds" in str(exc)
    else:
        raise AssertionError("expected invalid bucket to raise")

    try:
        runtime.get_history(limit=10, bucket_seconds=60, metric="utilization_gpu", agg="median")
    except ValueError as exc:
        assert "agg must be" in str(exc)
    else:
        raise AssertionError("expected invalid agg to raise")
