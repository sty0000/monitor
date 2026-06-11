from pathlib import Path

import pytest

from monitor.config import PlatformConfig
from monitor.init_config import (
    check_python_env,
    InitDetection,
    InitOptions,
    build_config,
    build_env_entries,
    build_service,
    detect_environment,
    generate_files,
    install_systemd_files,
    run_post_install_checks,
    render_conda_setup,
    uninstall_systemd_files,
)


class StubCollector:
    def __init__(self, profile: str) -> None:
        self.profile = profile

    def detect_platform(self, platform_config: PlatformConfig, timeout_seconds: int):
        return {
            "profile": self.profile,
            "configured_profile": "auto",
            "detected_profile": self.profile,
            "arch": "aarch64",
            "os": "Ubuntu",
            "driver_version": "580.159.03",
            "gpu_inventory": [],
            "dgx_dashboard_present": self.profile == "dgx_spark",
            "dcgm_available": True,
            "telemetry_source_active": "nvidia_smi",
            "telemetry_source_error": "",
        }


def _detection(tmp_path: Path, profile: str = "generic_nvidia") -> InitDetection:
    return InitDetection(
        project_dir=tmp_path,
        python_path="/opt/conda/envs/monitor/bin/python",
        user="sunnytan",
        group="sunnytan",
        hostname="gx10-b07b",
        nvidia_smi_path="/usr/bin/nvidia-smi",
        dcgmi_path="/usr/bin/dcgmi",
        platform_summary={"profile": profile},
        recommended_port=8093 if profile == "dgx_spark" else 8090,
    )


def _write_examples(tmp_path: Path) -> tuple[Path, Path]:
    example = tmp_path / "config.example.yaml"
    env_example = tmp_path / "gpu-monitor.env.example"
    example.write_text(
        """
monitor:
  instance_name: gpu-monitor
platform:
  profile: auto
  telemetry_order: [dcgm, nvidia_smi]
dashboard:
  host: 127.0.0.1
  port: 8090
  auth:
    enabled: true
    token: CHANGE_ME_BEARER_TOKEN
notify:
  control:
    enabled: true
    low_usage_enabled: true
""",
        encoding="utf-8",
    )
    env_example.write_text(
        """# env
GPU_MONITOR_NOTIFY_ENABLED=true
GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED=true
GPU_MONITOR_DASHBOARD_AUTH_TOKEN=CHANGE_ME
GPU_MONITOR_DASHBOARD_HOST=127.0.0.1
GPU_MONITOR_DASHBOARD_PORT=8090
""",
        encoding="utf-8",
    )
    return example, env_example


def test_detect_environment_uses_dgx_spark_port() -> None:
    detection = detect_environment(collector=StubCollector("dgx_spark"), python_path="/x/python")
    assert detection.recommended_port == 8093
    assert detection.python_path == "/x/python"


def test_detect_environment_uses_generic_port() -> None:
    detection = detect_environment(collector=StubCollector("generic_nvidia"), python_path="/x/python")
    assert detection.recommended_port == 8090


def test_build_service_uses_detected_python_user_and_group(tmp_path: Path) -> None:
    detection = _detection(tmp_path)
    service = build_service(detection, tmp_path / "config.yaml")
    assert "ExecStart=/opt/conda/envs/monitor/bin/python -m monitor.dashboard" in service
    assert "User=sunnytan" in service
    assert "Group=sunnytan" in service
    assert f"WorkingDirectory={tmp_path}" in service


def test_build_config_and_env_apply_options(tmp_path: Path) -> None:
    example, env_example = _write_examples(tmp_path)
    detection = _detection(tmp_path, "dgx_spark")
    options = InitOptions(
        instance_name="gx10-b07b",
        host="127.0.0.1",
        port=8093,
        notify_enabled=False,
        low_usage_notify_enabled=False,
        token="secret-token",
    )
    config = build_config(example, detection, options)
    env = build_env_entries(env_example, options)
    assert config["monitor"]["instance_name"] == "gx10-b07b"
    assert config["dashboard"]["port"] == 8093
    assert config["notify"]["control"]["enabled"] is False
    assert ("GPU_MONITOR_DASHBOARD_AUTH_TOKEN", "secret-token") in env
    assert ("GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED", "false") in env


def test_generate_files_dry_run_does_not_write(tmp_path: Path) -> None:
    example, env_example = _write_examples(tmp_path)
    detection = _detection(tmp_path)
    options = InitOptions("host-a", "127.0.0.1", 8090, True, True, "token")
    output_config = tmp_path / "config.yaml"
    outputs = generate_files(
        detection,
        options,
        example,
        env_example,
        output_config,
        tmp_path / "env.new",
        tmp_path / "service.new",
        dry_run=True,
    )
    assert str(output_config) in outputs
    assert not output_config.exists()


def test_generate_files_refuses_overwrite_without_force(tmp_path: Path) -> None:
    example, env_example = _write_examples(tmp_path)
    detection = _detection(tmp_path)
    options = InitOptions("host-a", "127.0.0.1", 8090, True, True, "token")
    output_config = tmp_path / "config.yaml"
    output_config.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        generate_files(
            detection,
            options,
            example,
            env_example,
            output_config,
            tmp_path / "env.new",
            tmp_path / "service.new",
        )
    forced = InitOptions("host-a", "127.0.0.1", 8090, True, True, "token", force=True)
    generate_files(detection, forced, example, env_example, output_config, tmp_path / "env.new", tmp_path / "service.new")
    assert "host-a" in output_config.read_text(encoding="utf-8")

def test_install_systemd_files_backs_up_and_runs_commands(tmp_path: Path) -> None:
    env_source = tmp_path / "env.new"
    service_source = tmp_path / "service.new"
    system_env = tmp_path / "etc" / "default" / "gpu-monitor"
    system_service = tmp_path / "etc" / "systemd" / "system" / "gpu-monitor-dashboard.service"
    env_source.write_text("TOKEN=new\n", encoding="utf-8")
    service_source.write_text("[Service]\nExecStart=new\n", encoding="utf-8")
    system_env.parent.mkdir(parents=True)
    system_service.parent.mkdir(parents=True)
    system_env.write_text("TOKEN=old\n", encoding="utf-8")
    system_service.write_text("old service\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, check):
        commands.append(command)

    actions = install_systemd_files(env_source, service_source, system_env, system_service, run_command=fake_run, use_sudo=False)

    assert system_env.read_text(encoding="utf-8") == "TOKEN=new\n"
    assert system_service.read_text(encoding="utf-8") == "[Service]\nExecStart=new\n"
    assert list(system_env.parent.glob("gpu-monitor.bak.*"))
    assert list(system_service.parent.glob("gpu-monitor-dashboard.service.bak.*"))
    assert commands == [["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", "gpu-monitor-dashboard"]]
    assert any("installed" in action for action in actions)


def test_dry_run_with_install_systemd_does_not_write_or_install(tmp_path: Path) -> None:
    example, env_example = _write_examples(tmp_path)
    detection = _detection(tmp_path)
    options = InitOptions("host-a", "127.0.0.1", 8090, True, True, "token")
    output_config = tmp_path / "config.yaml"
    output_env = tmp_path / "env.new"
    output_service = tmp_path / "service.new"

    generate_files(detection, options, example, env_example, output_config, output_env, output_service, dry_run=True)

    assert not output_config.exists()
    assert not output_env.exists()
    assert not output_service.exists()


def test_install_systemd_files_uses_sudo_commands(tmp_path: Path) -> None:
    env_source = tmp_path / "env.new"
    service_source = tmp_path / "service.new"
    system_env = tmp_path / "etc" / "default" / "gpu-monitor"
    system_service = tmp_path / "etc" / "systemd" / "system" / "gpu-monitor-dashboard.service"
    env_source.write_text("TOKEN=new\n", encoding="utf-8")
    service_source.write_text("service\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, check):
        commands.append(command)

    install_systemd_files(env_source, service_source, system_env, system_service, run_command=fake_run, use_sudo=True)

    assert ["sudo", "mkdir", "-p", str(system_env.parent), str(system_service.parent)] in commands
    assert ["sudo", "cp", str(env_source), str(system_env)] in commands
    assert ["sudo", "cp", str(service_source), str(system_service)] in commands
    assert ["sudo", "systemctl", "daemon-reload"] in commands
    assert ["sudo", "systemctl", "enable", "--now", "gpu-monitor-dashboard"] in commands



def test_run_post_install_checks_delegates_to_doctor(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_run_doctor(config_path, env_path, test_notify=False):
        calls.append((config_path, env_path, test_notify))
        return []

    monkeypatch.setattr("monitor.init_config.run_doctor", fake_run_doctor)
    checks = run_post_install_checks(tmp_path / "config.yaml", tmp_path / "env", test_notify=True, wait_seconds=0)

    assert checks == []
    assert calls == [(tmp_path / "config.yaml", tmp_path / "env", True)]



def test_uninstall_systemd_files_backs_up_without_removing_by_default(tmp_path: Path) -> None:
    system_env = tmp_path / "etc" / "default" / "gpu-monitor"
    system_service = tmp_path / "etc" / "systemd" / "system" / "gpu-monitor-dashboard.service"
    system_env.parent.mkdir(parents=True)
    system_service.parent.mkdir(parents=True)
    system_env.write_text("TOKEN=old\n", encoding="utf-8")
    system_service.write_text("service\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, check):
        commands.append(command)

    actions = uninstall_systemd_files(system_env, system_service, run_command=fake_run, use_sudo=False)

    assert system_env.exists()
    assert system_service.exists()
    assert any("backed up" in action for action in actions)
    assert ["systemctl", "stop", "gpu-monitor-dashboard"] in commands
    assert ["systemctl", "disable", "gpu-monitor-dashboard"] in commands
    assert ["systemctl", "daemon-reload"] in commands


def test_uninstall_systemd_files_can_remove_after_backup(tmp_path: Path) -> None:
    system_env = tmp_path / "gpu-monitor"
    system_service = tmp_path / "gpu-monitor-dashboard.service"
    system_env.write_text("TOKEN=old\n", encoding="utf-8")
    system_service.write_text("service\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(command, check):
        commands.append(command)

    actions = uninstall_systemd_files(system_env, system_service, run_command=fake_run, use_sudo=False, remove_system_files=True)

    assert not system_env.exists()
    assert not system_service.exists()


def test_init_config_upgrade_config_dry_run(monkeypatch, tmp_path: Path, capsys) -> None:
    from monitor import init_config

    example_path = tmp_path / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    example_path.write_text("monitor:\n  interval_seconds: 15\n", encoding="utf-8")
    config_path.write_text("monitor:\n  instance_name: old\n", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "init_config",
            "--upgrade-config",
            "--dry-run",
            "--example",
            str(example_path),
            "--output-config",
            str(config_path),
        ],
    )

    assert init_config.main() == 0
    output = capsys.readouterr().out
    assert "Config migration preview" in output
    assert "monitor.interval_seconds" in output


def test_uninstall_systemd_files_dry_run_does_not_change_files(tmp_path: Path) -> None:
    system_env = tmp_path / "gpu-monitor"
    system_service = tmp_path / "gpu-monitor-dashboard.service"
    system_env.write_text("TOKEN=old\n", encoding="utf-8")
    system_service.write_text("service\n", encoding="utf-8")

    actions = uninstall_systemd_files(system_env, system_service, use_sudo=False, remove_system_files=True, dry_run=True)

    assert system_env.exists()
    assert system_service.exists()
    assert any(action.startswith("would remove") for action in actions)


def test_render_conda_setup_contains_safe_commands() -> None:
    text = render_conda_setup()

    assert "conda create -n monitor python=3.10 -y" in text
    assert "pip install -r requirements.txt" in text


def test_check_python_env_reports_current_python() -> None:
    checks = check_python_env()

    assert any(item.startswith("Python:") for item in checks)



def test_build_service_uses_hardened_runtime_defaults(tmp_path: Path) -> None:
    service_text = build_service(_detection(tmp_path), tmp_path / "config.yaml")

    assert "Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in service_text
    assert "ProtectHome=false" in service_text
    assert "Restart=always" in service_text
    assert "RestartSec=5" in service_text
    assert "KillSignal=SIGINT" in service_text
    assert "SuccessExitStatus=143" in service_text
