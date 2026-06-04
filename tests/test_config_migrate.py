from pathlib import Path

import yaml

from monitor.config_migrate import build_merged_config, build_merged_env, write_merged_config, write_merged_env


def test_build_merged_config_keeps_old_values_and_adds_new_keys(tmp_path: Path) -> None:
    example_path = tmp_path / "config.example.yaml"
    old_path = tmp_path / "old.yaml"
    example_path.write_text(
        """
monitor:
  instance_name: "gpu-monitor"
  interval_seconds: 15
threshold:
  usage_percent: 20
  high_temperature_c: 85
notify:
  control:
    enabled: true
    low_usage_enabled: true
  wecom:
    enabled: true
    webhook_url: "REPLACE_ME"
""",
        encoding="utf-8",
    )
    old_path.write_text(
        """
monitor:
  instance_name: "server-a"
threshold:
  usage_percent: 10
notify:
  control:
    enabled: false
  wecom:
    webhook_url: "https://example.com/hook"
legacy_only: "drop me"
""",
        encoding="utf-8",
    )

    merged = build_merged_config(example_path, old_path)

    assert merged["monitor"]["instance_name"] == "server-a"
    assert merged["monitor"]["interval_seconds"] == 15
    assert merged["threshold"]["usage_percent"] == 10
    assert merged["threshold"]["high_temperature_c"] == 85
    assert merged["notify"]["control"]["enabled"] is False
    assert merged["notify"]["control"]["low_usage_enabled"] is True
    assert merged["notify"]["wecom"]["enabled"] is True
    assert merged["notify"]["wecom"]["webhook_url"] == "https://example.com/hook"
    assert "legacy_only" not in merged


def test_write_merged_config_refuses_overwrite_without_force(tmp_path: Path) -> None:
    example_path = tmp_path / "config.example.yaml"
    old_path = tmp_path / "old.yaml"
    output_path = tmp_path / "config.yaml"
    example_path.write_text("monitor:\n  instance_name: example\n", encoding="utf-8")
    old_path.write_text("monitor:\n  instance_name: old\n", encoding="utf-8")
    output_path.write_text("existing", encoding="utf-8")

    try:
        write_merged_config(example_path, old_path, output_path)
        assert False, "expected FileExistsError"
    except FileExistsError:
        pass

    write_merged_config(example_path, old_path, output_path, force=True)
    data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert data["monitor"]["instance_name"] == "old"

def test_build_merged_env_keeps_old_values_and_adds_new_keys(tmp_path: Path) -> None:
    example_path = tmp_path / "gpu-monitor.env.example"
    old_path = tmp_path / "gpu-monitor.env"
    example_path.write_text(
        """# env example
GPU_MONITOR_NOTIFY_ENABLED=true
GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED=true
GPU_MONITOR_DASHBOARD_AUTH_TOKEN=CHANGE_ME
GPU_MONITOR_DASHBOARD_PORT=8090
""",
        encoding="utf-8",
    )
    old_path.write_text(
        """# old env
GPU_MONITOR_NOTIFY_ENABLED=false
GPU_MONITOR_DASHBOARD_AUTH_TOKEN=old-token
OLD_UNUSED=value
""",
        encoding="utf-8",
    )

    merged = build_merged_env(example_path, old_path)

    assert merged == [
        ("# env example", None),
        ("GPU_MONITOR_NOTIFY_ENABLED", "false"),
        ("GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED", "true"),
        ("GPU_MONITOR_DASHBOARD_AUTH_TOKEN", "old-token"),
        ("GPU_MONITOR_DASHBOARD_PORT", "8090"),
    ]


def test_write_merged_env_quotes_values_when_needed(tmp_path: Path) -> None:
    example_path = tmp_path / "gpu-monitor.env.example"
    old_path = tmp_path / "gpu-monitor.env"
    output_path = tmp_path / "gpu-monitor.env.new"
    example_path.write_text("TOKEN=CHANGE_ME\nHOST=127.0.0.1\n", encoding="utf-8")
    old_path.write_text("TOKEN=old token with spaces\n", encoding="utf-8")

    write_merged_env(example_path, old_path, output_path)

    assert output_path.read_text(encoding="utf-8") == 'TOKEN="old token with spaces"\nHOST=127.0.0.1\n'
