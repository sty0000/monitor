from __future__ import annotations

from pathlib import Path

from monitor.doctor import DoctorCheck, check_dcgm_service, check_history_file, check_systemd_runtime, check_systemd_service_file, render_checks, run_doctor


def _write_config(path: Path, port: int = 8090) -> None:
    path.write_text(
        f'''
monitor:
  instance_name: test-host
dashboard:
  host: 127.0.0.1
  port: {port}
  auth:
    enabled: true
    token: config-token
platform:
  profile: auto
  telemetry_order: [dcgm, nvidia_smi]
''',
        encoding="utf-8",
    )


def test_render_checks_includes_hints_and_summary() -> None:
    text = render_checks([
        DoctorCheck("config", "ok", "loaded"),
        DoctorCheck("dcgmi", "warn", "not found", "fallback to nvidia-smi"),
        DoctorCheck("deps", "error", "missing", "pip install -r requirements.txt"),
    ])

    assert "[OK] config: loaded" in text
    assert "[WARN] dcgmi: not found" in text
    assert "hint: fallback to nvidia-smi" in text
    assert "summary: errors=1 warnings=1" in text


def test_run_doctor_reports_missing_config(tmp_path: Path) -> None:
    checks = run_doctor(
        config_path=tmp_path / "missing.yaml",
        env_path=tmp_path / "missing.env",
        skip_network_checks=True,
    )

    by_name = {check.name: check for check in checks}
    assert by_name["config"].status == "warn"
    assert by_name["env_file"].status == "warn"


def test_run_doctor_loads_config_and_env_without_network(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "gpu-monitor.env"
    _write_config(config_path)
    env_path.write_text("GPU_MONITOR_DASHBOARD_AUTH_TOKEN=env-token\n", encoding="utf-8")

    checks = run_doctor(config_path=config_path, env_path=env_path, skip_network_checks=True)

    by_name = {check.name: check for check in checks}
    assert by_name["config"].status == "ok"
    assert by_name["env_file"].status == "ok"
    assert "api_health" not in by_name


def test_check_systemd_service_file_detects_common_exec_risks(tmp_path: Path) -> None:
    service = tmp_path / "gpu-monitor-dashboard.service"
    env_file = tmp_path / "gpu-monitor.env"
    config_path = tmp_path / "missing-config.yaml"
    service.write_text(
        "\n".join([
            "[Service]",
            "WorkingDirectory=/home/ubuntu/monitor",
            f"ExecStart=/missing/python -m monitor.dashboard --config {config_path}",
            f"EnvironmentFile={env_file}",
            "ProtectHome=true",
            "User=ubuntu",
            "Group=ubuntu",
            "",
        ]),
        encoding="utf-8",
    )

    checks = check_systemd_service_file(service, env_file)
    by_name = {check.name: check for check in checks}

    assert by_name["systemd_service_file"].status == "ok"
    assert by_name["service_execstart_python"].status == "warn"
    assert by_name["service_execstart_config"].status == "warn"
    assert by_name["service_environment_file"].status == "warn"
    assert by_name["service_path"].status == "warn"
    assert by_name["protect_home"].status == "warn"


def test_run_doctor_accepts_service_file_override(tmp_path: Path) -> None:
    import sys

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "gpu-monitor.env"
    service_path = tmp_path / "service.service"
    _write_config(config_path)
    env_path.write_text("GPU_MONITOR_DASHBOARD_AUTH_TOKEN=env-token\n", encoding="utf-8")
    service_path.write_text(
        "\n".join([
            "[Service]",
            f"WorkingDirectory={tmp_path}",
            f"ExecStart={sys.executable} -m monitor.dashboard --config {config_path}",
            "Environment=PATH=/usr/local/bin:/usr/bin:/bin",
            f"EnvironmentFile={env_path}",
            "ProtectHome=false",
            "ProtectSystem=full",
            "User=test",
            "Group=test",
            "",
        ]),
        encoding="utf-8",
    )

    checks = run_doctor(config_path=config_path, env_path=env_path, service_file=service_path, skip_network_checks=True)
    by_name = {check.name: check for check in checks}

    assert by_name["systemd_service_file"].status == "ok"
    assert by_name["service_execstart"].status == "ok"
    assert by_name["service_environment_file"].status == "ok"


def test_check_systemd_runtime_reports_show_values(monkeypatch, tmp_path: Path) -> None:
    service = tmp_path / "svc.service"
    service.write_text("WorkingDirectory=/tmp\nUser=a\nGroup=b\n", encoding="utf-8")

    def fake_run(command, timeout_seconds=5):
        if command[:2] == ["systemctl", "is-active"]:
            return True, "active"
        if command[:2] == ["systemctl", "show"]:
            return True, "MainPID=123\nExecMainStatus=0\nResult=success\nFragmentPath=" + str(service) + "\nWorkingDirectory=/tmp\nUser=a\nGroup=b\n"
        return False, "bad"

    monkeypatch.setattr("monitor.doctor._run_command", fake_run)
    checks = check_systemd_runtime("gpu-monitor-dashboard", service)
    by_name = {check.name: check for check in checks}

    assert by_name["systemd_active"].status == "ok"
    assert by_name["systemd_show"].status == "ok"
    assert by_name["service_runtime_user"].status == "ok"


def test_check_dcgm_service_accepts_nvidia_dcgm(monkeypatch) -> None:
    def fake_run(command, timeout_seconds=5):
        return (True, "active") if command[-1] == "nvidia-dcgm" else (False, "inactive")

    monkeypatch.setattr("monitor.doctor._run_command", fake_run)
    checks = check_dcgm_service()

    assert checks[0].name == "dcgm_service"
    assert checks[0].status == "ok"


def test_check_history_file_reports_bad_lines(tmp_path: Path) -> None:
    history_path = tmp_path / "history.jsonl"
    history_path.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")
    config = type("Config", (), {"history": type("History", (), {"enabled": True, "path": str(history_path)})()})()

    checks = check_history_file(config)
    by_name = {check.name: check for check in checks}

    assert by_name["history_file"].status == "warn"
    assert "bad_lines=1" in by_name["history_file"].message
