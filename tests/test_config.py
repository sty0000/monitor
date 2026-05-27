from pathlib import Path

from monitor.config import ConfigError, load_config


def test_load_config_reads_new_schema(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
monitor:
  instance_name: "server-a"
  interval_seconds: 20
  command_timeout_seconds: 7
dashboard:
  auth:
    enabled: true
    token: "secret-token"
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.monitor.instance_name == "server-a"
    assert config.monitor.interval_seconds == 20
    assert config.monitor.command_timeout_seconds == 7
    assert config.dashboard.auth.token == "secret-token"
    assert config.threshold.high_temperature_c == 85
    assert config.threshold.high_temperature_minutes == 3
    assert config.alert.runtime_error.enabled is True
    assert config.alert.runtime_error.consecutive_failures == 3


def test_load_config_requires_token_when_auth_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard:\n  auth:\n    enabled: true\n", encoding="utf-8")
    try:
        load_config(config_path)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_load_config_reads_temperature_thresholds(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
threshold:
  high_temperature_c: 88
  high_temperature_minutes: 2.5
dashboard:
  auth:
    enabled: true
    token: "secret-token"
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.threshold.high_temperature_c == 88
    assert config.threshold.high_temperature_minutes == 2.5



def test_load_config_reads_runtime_error_alert(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
alert:
  runtime_error:
    enabled: true
    consecutive_failures: 5
    cooldown_minutes: 10
dashboard:
  auth:
    enabled: true
    token: "secret-token"
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.alert.runtime_error.enabled is True
    assert config.alert.runtime_error.consecutive_failures == 5
    assert config.alert.runtime_error.cooldown_minutes == 10
