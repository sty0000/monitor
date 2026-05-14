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


def test_load_config_requires_token_when_auth_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard:\n  auth:\n    enabled: true\n", encoding="utf-8")
    try:
        load_config(config_path)
        assert False, "expected ConfigError"
    except ConfigError:
        pass
