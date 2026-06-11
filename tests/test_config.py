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


def test_load_config_reads_platform_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
platform:
  profile: "dgx_spark"
  telemetry_order: ["dcgm", "nvidia_smi"]
dashboard:
  auth:
    enabled: true
    token: "secret-token"
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.platform.profile == "dgx_spark"
    assert config.platform.telemetry_order == ["dcgm", "nvidia_smi"]


def test_load_config_defaults_platform_auto(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: true
    token: "secret-token"
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.platform.profile == "auto"
    assert config.platform.telemetry_order == ["dcgm", "nvidia_smi"]



def test_load_config_reads_history_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
history:
  enabled: true
  path: logs/custom-history.jsonl
  max_points_memory: 10
  max_file_mb: 1
  persist_interval_seconds: 2
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.history.path == "logs/custom-history.jsonl"
    assert config.history.max_points_memory == 10
    assert config.history.persist_interval_seconds == 2
    assert config.history.retention_days == 30
    assert config.history.rotate_keep_files == 5
    assert config.history.query_default_limit == 240



def test_load_config_rejects_unknown_notify_strategy_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
notify:
  strategy:
    mode: broadcast
""",
        encoding="utf-8",
    )

    try:
        load_config(path)
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "notify.strategy.mode" in str(exc)


def test_load_config_rejects_empty_notify_strategy_order(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
notify:
  strategy:
    order: []
""",
        encoding="utf-8",
    )

    try:
        load_config(path)
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "notify.strategy.order" in str(exc)


def test_load_config_rejects_unknown_notify_strategy_channel(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
notify:
  strategy:
    order: [wecom, unknown]
""",
        encoding="utf-8",
    )

    try:
        load_config(path)
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "unknown" in str(exc)


def test_config_warnings_report_unsafe_public_dashboard_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  host: 0.0.0.0
  auth:
    enabled: false
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert "auth disabled" in config.warnings()[0]


def test_load_config_rejects_empty_history_and_logging_paths(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
history:
  path: ""
""",
        encoding="utf-8",
    )

    try:
        load_config(path)
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "history.path" in str(exc)



def test_load_config_rejects_unknown_low_usage_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
threshold:
  low_usage_mode: sometimes
""",
        encoding="utf-8",
    )

    try:
        load_config(path)
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "threshold.low_usage_mode" in str(exc)


def test_minimal_example_config_loads() -> None:
    config = load_config(Path("config.minimal.example.yaml"))

    assert config.monitor.instance_name == "gpu-node"
    assert config.dashboard.host == "127.0.0.1"
    assert config.dashboard.auth.enabled is True
    assert config.notify.control.enabled is False
    assert config.notify.webhook.enabled is False
    assert config.notify.wecom.enabled is False
    assert config.notify.feishu.enabled is False
    assert config.notify.dingtalk.enabled is False
    assert config.notify.telegram.enabled is False
    assert config.notify.smtp.enabled is False
    assert config.history.retention_days == 7


def test_load_config_reads_scenario_profile_without_changing_security_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
scenario_profile: inference
dashboard:
  host: "127.0.0.1"
  auth:
    enabled: true
    token: "secret-token"
notify:
  control:
    enabled: false
  webhook:
    enabled: false
    url: "https://example.com/webhook"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.scenario_profile == "inference"
    assert config.dashboard.host == "127.0.0.1"
    assert config.dashboard.auth.token == "secret-token"
    assert config.notify.control.enabled is False
    assert config.notify.webhook.enabled is False


def test_load_config_rejects_unknown_scenario_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
scenario_profile: batch_scheduler
dashboard:
  auth:
    enabled: false
""",
        encoding="utf-8",
    )

    try:
        load_config(config_path)
    except ConfigError as exc:
        assert "scenario_profile" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_load_config_accepts_user_profile_template(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
scenario_profile: llm_serving
profile_templates:
  llm_serving:
    description: LLM serving
    threshold:
      usage_percent: 10
      idle_minutes: 30
      no_process_minutes: 10
      low_usage_mode: all
    scenario_messages:
      low_usage_hint: low traffic
      no_process_hint: serving stopped
dashboard:
  auth:
    enabled: false
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.scenario_profile == "llm_serving"
    assert config.profile_templates["llm_serving"]["description"] == "LLM serving"


def test_load_config_rejects_unknown_profile_template_field(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
scenario_profile: llm_serving
profile_templates:
  llm_serving:
    threshold:
      gpu_magic: 1
dashboard:
  auth:
    enabled: false
""",
        encoding="utf-8",
    )

    try:
        load_config(config_path)
    except ConfigError as exc:
        assert "unknown field" in str(exc)
    else:
        raise AssertionError("expected ConfigError")
