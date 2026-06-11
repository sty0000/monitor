from pathlib import Path

import yaml

from monitor.config_migrate import apply_migration, build_merged_config, build_merged_env, build_migration_plan, render_migration_preview, write_merged_config, write_merged_env, write_migration_output


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


def test_migration_plan_adds_missing_fields_and_preserves_unknown(tmp_path: Path) -> None:
    example_path = tmp_path / "config.example.yaml"
    old_path = tmp_path / "config.yaml"
    example_path.write_text(
        """
monitor:
  instance_name: gpu-monitor
  interval_seconds: 15
dashboard:
  auth:
    enabled: true
    token: CHANGE_ME
notify:
  wecom:
    webhook_url: REPLACE_ME
""",
        encoding="utf-8",
    )
    old_path.write_text(
        """
monitor:
  instance_name: server-a
legacy_block:
  old_key: keep-me
dashboard:
  auth:
    token: real-token
notify:
  wecom:
    webhook_url: https://example.com/hook
""",
        encoding="utf-8",
    )

    plan = build_migration_plan(example_path, old_path)
    preview = render_migration_preview(plan)

    assert plan.merged_config["monitor"]["instance_name"] == "server-a"
    assert plan.merged_config["monitor"]["interval_seconds"] == 15
    assert plan.merged_config["legacy_block"]["old_key"] == "keep-me"
    assert "monitor.interval_seconds" in plan.added_paths
    assert "legacy_block.old_key" in plan.preserved_unknown_paths
    assert "legacy_block.old_key" in plan.removed_paths
    assert "real-token" not in preview
    assert "https://example.com/hook" not in preview
    assert "***REDACTED***" in preview


def test_migration_plan_reports_type_warnings(tmp_path: Path) -> None:
    example_path = tmp_path / "config.example.yaml"
    old_path = tmp_path / "config.yaml"
    example_path.write_text("monitor:\n  interval_seconds: 15\n", encoding="utf-8")
    old_path.write_text("monitor:\n  interval_seconds:\n    bad: value\n", encoding="utf-8")

    plan = build_migration_plan(example_path, old_path)

    assert plan.merged_config["monitor"]["interval_seconds"] == 15
    assert any("monitor.interval_seconds" in warning for warning in plan.type_warnings)


def test_write_migration_output_refuses_overwrite_without_force(tmp_path: Path) -> None:
    example_path = tmp_path / "config.example.yaml"
    old_path = tmp_path / "config.yaml"
    output_path = tmp_path / "config.upgraded.yaml"
    example_path.write_text("monitor:\n  instance_name: example\n", encoding="utf-8")
    old_path.write_text("monitor:\n  instance_name: old\n", encoding="utf-8")
    output_path.write_text("exists", encoding="utf-8")
    plan = build_migration_plan(example_path, old_path)

    try:
        write_migration_output(plan, output_path)
        assert False, "expected FileExistsError"
    except FileExistsError:
        pass


def test_apply_migration_backs_up_and_replaces_config(tmp_path: Path) -> None:
    example_path = tmp_path / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    example_path.write_text("monitor:\n  instance_name: example\n  interval_seconds: 15\n", encoding="utf-8")
    config_path.write_text("monitor:\n  instance_name: old\n", encoding="utf-8")
    plan = build_migration_plan(example_path, config_path)

    backup_path = apply_migration(plan, config_path, backup_dir=backup_dir)

    assert backup_path.exists()
    assert yaml.safe_load(backup_path.read_text(encoding="utf-8"))["monitor"]["instance_name"] == "old"
    upgraded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert upgraded["monitor"]["interval_seconds"] == 15


def test_apply_migration_backup_failure_does_not_modify_config(tmp_path: Path, monkeypatch) -> None:
    example_path = tmp_path / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    example_path.write_text("monitor:\n  interval_seconds: 15\n", encoding="utf-8")
    config_path.write_text("monitor:\n  instance_name: old\n", encoding="utf-8")
    before = config_path.read_text(encoding="utf-8")
    plan = build_migration_plan(example_path, config_path)

    def fail_copy(*args, **kwargs):
        raise OSError("backup failed")

    monkeypatch.setattr("monitor.config_migrate.shutil.copy2", fail_copy)

    try:
        apply_migration(plan, config_path)
        assert False, "expected OSError"
    except OSError:
        pass

    assert config_path.read_text(encoding="utf-8") == before


def test_apply_migration_write_failure_rolls_back(tmp_path: Path, monkeypatch) -> None:
    example_path = tmp_path / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    example_path.write_text("monitor:\n  interval_seconds: 15\n", encoding="utf-8")
    config_path.write_text("monitor:\n  instance_name: old\n", encoding="utf-8")
    before = config_path.read_text(encoding="utf-8")
    plan = build_migration_plan(example_path, config_path)

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("monitor.config_migrate.os.replace", fail_replace)

    try:
        apply_migration(plan, config_path)
        assert False, "expected OSError"
    except OSError:
        pass

    assert config_path.read_text(encoding="utf-8") == before


def test_config_migrate_cli_dry_run_does_not_write(tmp_path: Path, capsys) -> None:
    from monitor.config_migrate import main

    example_path = tmp_path / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "out.yaml"
    example_path.write_text("dashboard:\n  auth:\n    token: CHANGE_ME\nmonitor:\n  interval_seconds: 15\n", encoding="utf-8")
    config_path.write_text("dashboard:\n  auth:\n    token: real-token\n", encoding="utf-8")

    import sys

    old_argv = sys.argv
    sys.argv = ["config_migrate", "--old", str(config_path), "--example", str(example_path), "--output", str(output_path), "--dry-run"]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    captured = capsys.readouterr().out
    assert not output_path.exists()
    assert "real-token" not in captured
    assert "***REDACTED***" in captured
