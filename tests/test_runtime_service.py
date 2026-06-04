from pathlib import Path

from monitor.runtime_service import MonitorRuntimeService


class CapturingNotificationService:
    active_channels = ["test"]

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, subject: str, body: str, channel_name: str | None = None):
        self.messages.append((subject, body))
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
    runtime._maybe_send_alert(evaluation, {"gpus": []}, 1000)

    assert notifier.messages == []
    assert runtime.state.active_alert is None
