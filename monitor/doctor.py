from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .collector import GPUCollector
from .config import ConfigError, PlatformConfig, load_config


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    message: str
    hint: str = ""


def _check(name: str, status: str, message: str, hint: str = "") -> DoctorCheck:
    return DoctorCheck(name=name, status=status, message=message, hint=hint)


def _run_command(command: list[str], timeout_seconds: int = 5) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return False, "command not found"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout_seconds}s"
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return completed.returncode == 0, output


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _port_is_open(host: str, port: int, timeout_seconds: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _http_json(url: str, token: str = "", method: str = "GET", timeout_seconds: float = 3.0) -> tuple[bool, str, Any | None]:
    data = b"{}" if method.upper() == "POST" else None
    request = urllib.request.Request(url, data=data, method=method.upper())
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else None
            return 200 <= response.status < 300, f"HTTP {response.status}", parsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {body[:200]}", None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), None



def _http_status(url: str, token: str = "", timeout_seconds: float = 3.0) -> tuple[bool, str]:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "text/plain")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read(256)
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {body[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)



def _parse_service_file(path: Path) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), []).append(value.strip())
    return values


def _first_service_value(values: dict[str, list[str]], key: str) -> str:
    items = values.get(key) or []
    return items[-1] if items else ""


def _extract_execstart_parts(execstart: str) -> tuple[str, str]:
    parts = execstart.split()
    python_path = parts[0] if parts else ""
    config_path = ""
    for index, part in enumerate(parts):
        if part == "--config" and index + 1 < len(parts):
            config_path = parts[index + 1]
            break
    return python_path, config_path


def _parse_systemctl_show(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in output.splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def check_systemd_runtime(service_name: str, service_file: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    ok_active, active_output = _run_command(["systemctl", "is-active", service_name], timeout_seconds=5)
    checks.append(_check("systemd_active", "ok" if ok_active else "warn", active_output or f"{service_name} inactive", f"运行 sudo systemctl status {service_name} --no-pager" if not ok_active else ""))

    ok_show, show_output = _run_command(["systemctl", "show", service_name, "--property=MainPID,ExecMainStatus,Result,FragmentPath,WorkingDirectory,User,Group,ExecStart,EnvironmentFiles"], timeout_seconds=5)
    if not ok_show:
        checks.append(_check("systemd_show", "warn", show_output or "systemctl show failed", "确认 systemd 可用，或使用 --skip-systemd-checks 跳过"))
        return checks

    values = _parse_systemctl_show(show_output)
    summary = " ".join(f"{key}={values.get(key, '')}" for key in ["MainPID", "ExecMainStatus", "Result", "FragmentPath"] if key in values)
    checks.append(_check("systemd_show", "ok", summary or "systemctl show ok"))

    file_values = _parse_service_file(service_file)
    comparisons = {"WorkingDirectory": "service_runtime_working_directory", "User": "service_runtime_user", "Group": "service_runtime_group"}
    for key, check_name in comparisons.items():
        runtime_value = values.get(key, "")
        file_value = _first_service_value(file_values, key)
        if runtime_value and file_value and runtime_value != file_value:
            checks.append(_check(check_name, "warn", f"runtime={runtime_value} file={file_value}", "运行 systemctl daemon-reload 后重启服务"))
        elif runtime_value or file_value:
            checks.append(_check(check_name, "ok", runtime_value or file_value))

    runtime_fragment = values.get("FragmentPath", "")
    if runtime_fragment and service_file.exists() and Path(runtime_fragment) != service_file:
        checks.append(_check("service_fragment_path", "warn", runtime_fragment, f"当前检查文件是 {service_file}"))
    return checks


def check_dcgm_service() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for service_name in ["nvidia-dcgm", "dcgm"]:
        ok_active, output = _run_command(["systemctl", "is-active", service_name], timeout_seconds=5)
        if ok_active:
            return [_check("dcgm_service", "ok", f"{service_name} is active")]
        checks.append(_check(f"dcgm_service_{service_name}", "warn", output or f"{service_name} inactive", f"可尝试 sudo systemctl enable --now {service_name}"))
    return checks


def check_history_file(config: Any) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if config is None or not config.history.enabled:
        return [_check("history", "ok", "disabled")]
    path = Path(config.history.path)
    directory = path.parent if str(path.parent) else Path(".")
    checks.append(_check("history_directory", "ok" if directory.exists() else "warn", str(directory), "启动后会尝试创建目录；如失败请检查权限" if not directory.exists() else ""))
    if directory.exists():
        writable = os.access(directory, os.W_OK)
        checks.append(_check("history_directory_writable", "ok" if writable else "warn", str(directory), "确认 systemd User 可写 history 目录" if not writable else ""))
    if not path.exists():
        checks.append(_check("history_file", "warn", f"{path} not found", "服务首次写入后会创建；如果长期不存在请检查日志"))
        return checks
    bad_lines = 0
    total_lines = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        total_lines += 1
        try:
            json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
    size_mb = path.stat().st_size / 1024 / 1024
    checks.append(_check("history_file", "ok" if bad_lines == 0 else "warn", f"lines={total_lines} bad_lines={bad_lines} size_mb={size_mb:.2f}", "存在坏行时 monitor 会跳过，但建议检查磁盘/写入中断" if bad_lines else ""))
    checks.append(_check("history_file_mtime", "ok", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))))
    return checks


def check_systemd_service_file(service_file: Path, env_path: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if not service_file.exists():
        return [_check("systemd_service_file", "warn", f"{service_file} not found", "如果使用 systemd，请确认 service 已安装")]
    values = _parse_service_file(service_file)
    checks.append(_check("systemd_service_file", "ok", str(service_file)))

    working_directory = _first_service_value(values, "WorkingDirectory")
    if working_directory:
        wd = Path(working_directory)
        if wd.exists() and wd.is_dir():
            package_dir = wd / "monitor"
            checks.append(_check("service_working_directory", "ok" if package_dir.exists() else "warn", working_directory, "WorkingDirectory 下应包含 monitor package" if not package_dir.exists() else ""))
        else:
            checks.append(_check("service_working_directory", "warn", f"{working_directory} not found", "检查 WorkingDirectory 是否为项目目录"))
    else:
        checks.append(_check("service_working_directory", "warn", "WorkingDirectory not set", "建议设置为 monitor 项目根目录"))

    execstart = _first_service_value(values, "ExecStart")
    python_path, config_from_exec = _extract_execstart_parts(execstart)
    if not execstart:
        checks.append(_check("service_execstart", "error", "ExecStart not set", "service 必须设置 ExecStart"))
    else:
        contains_module = "-m monitor.dashboard" in execstart
        checks.append(_check("service_execstart", "ok" if contains_module else "warn", execstart, "ExecStart 应包含 -m monitor.dashboard" if not contains_module else ""))
        if python_path:
            python_exists = Path(python_path).exists()
            checks.append(_check("service_execstart_python", "ok" if python_exists else "warn", python_path, "检查 Conda/venv Python 路径；可用当前 sys.executable 重新生成 service" if not python_exists else ""))
        if config_from_exec:
            config_exists = Path(config_from_exec).exists()
            checks.append(_check("service_execstart_config", "ok" if config_exists else "warn", config_from_exec, "检查 --config 指向的 config.yaml 是否存在" if not config_exists else ""))

    service_env_files = values.get("EnvironmentFile") or []
    effective_env = Path(service_env_files[-1].lstrip("-") if service_env_files else str(env_path))
    if effective_env.exists():
        env_values = _read_env_file(effective_env)
        token_present = bool(env_values.get("GPU_MONITOR_DASHBOARD_AUTH_TOKEN"))
        checks.append(_check("service_environment_file", "ok" if token_present else "warn", str(effective_env), "设置 GPU_MONITOR_DASHBOARD_AUTH_TOKEN" if not token_present else ""))
    else:
        checks.append(_check("service_environment_file", "warn", f"{effective_env} not found", "检查 EnvironmentFile 路径"))

    env_lines = values.get("Environment") or []
    path_lines = [line for line in env_lines if line.startswith("PATH=")]
    if path_lines:
        service_path = path_lines[-1].split("=", 1)[1]
        missing_dirs = [item for item in ["/usr/bin", "/usr/local/bin"] if item not in service_path]
        checks.append(_check("service_path", "ok" if not missing_dirs else "warn", service_path, "PATH 建议包含 /usr/bin 和 /usr/local/bin" if missing_dirs else ""))
    else:
        checks.append(_check("service_path", "warn", "Environment=PATH not set", "建议添加系统 PATH，确保 nvidia-smi/dcgmi/ps 可被 systemd 找到"))

    protect_home = _first_service_value(values, "ProtectHome").lower()
    if working_directory.startswith("/home/") and protect_home in {"true", "yes", "read-only", "tmpfs"}:
        checks.append(_check("protect_home", "warn", f"ProtectHome={protect_home}", "home 目录部署请设置 ProtectHome=false"))
    elif protect_home:
        checks.append(_check("protect_home", "ok", f"ProtectHome={protect_home}"))

    for key in ["User", "Group", "ProtectSystem"]:
        value = _first_service_value(values, key)
        checks.append(_check(f"service_{key.lower()}", "ok" if value else "warn", value or f"{key} not set", f"建议设置 {key}" if not value else ""))

    return checks


def run_doctor(
    config_path: Path = Path("config.yaml"),
    env_path: Path = Path("/etc/default/gpu-monitor"),
    service_name: str = "gpu-monitor-dashboard",
    service_file: Path = Path("/etc/systemd/system/gpu-monitor-dashboard.service"),
    skip_network_checks: bool = False,
    test_notify: bool = False,
    skip_systemd_checks: bool = False,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    checks.append(_check("python", "ok", sys.executable))
    try:
        import flask  # noqa: F401
        import prometheus_client  # noqa: F401
        import yaml  # noqa: F401
        checks.append(_check("python_dependencies", "ok", "Flask/PyYAML/prometheus-client import ok"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("python_dependencies", "error", str(exc), "运行 pip install -r requirements.txt"))

    config = None
    if config_path.exists():
        try:
            config = load_config(config_path)
            checks.append(_check("config", "ok", str(config_path)))
        except ConfigError as exc:
            checks.append(_check("config", "error", str(exc), f"检查 {config_path}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("config", "error", str(exc), f"检查 {config_path}"))
    else:
        checks.append(_check("config", "warn", f"{config_path} not found", "先运行 python -m monitor.init_config"))

    env_values = _read_env_file(env_path)
    if env_path.exists():
        has_token = bool(env_values.get("GPU_MONITOR_DASHBOARD_AUTH_TOKEN"))
        checks.append(_check("env_file", "ok" if has_token else "warn", str(env_path), "设置 GPU_MONITOR_DASHBOARD_AUTH_TOKEN" if not has_token else ""))
    else:
        checks.append(_check("env_file", "warn", f"{env_path} not found", "如果使用 systemd，请安装到 /etc/default/gpu-monitor"))

    checks.extend(check_systemd_service_file(service_file, env_path))
    if not skip_systemd_checks:
        checks.extend(check_systemd_runtime(service_name, service_file))
        checks.extend(check_dcgm_service())

    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        ok, output = _run_command([nvidia_smi, "-L"], timeout_seconds=5)
        checks.append(_check("nvidia_smi", "ok" if ok else "error", output.splitlines()[0] if output else nvidia_smi, "检查 NVIDIA 驱动" if not ok else ""))
    else:
        checks.append(_check("nvidia_smi", "error", "nvidia-smi not found", "安装 NVIDIA 驱动或确认 systemd PATH"))

    dcgmi = shutil.which("dcgmi")
    if dcgmi:
        ok, output = _run_command([dcgmi, "discovery", "-l"], timeout_seconds=8)
        checks.append(_check("dcgmi", "ok" if ok else "warn", output.splitlines()[0] if output else dcgmi, "DCGM 不可用时 monitor 会回退到 nvidia-smi" if not ok else ""))
        ok_health, health_output = _run_command([dcgmi, "health", "-c"], timeout_seconds=8)
        health_hint = "如提示 watches 未启用，可运行 sudo dcgmi health -s a"
        checks.append(_check("dcgm_health", "ok" if ok_health else "warn", health_output.splitlines()[0] if health_output else "dcgmi health -c", "" if ok_health else health_hint))
    else:
        checks.append(_check("dcgmi", "warn", "dcgmi not found", "DGX Spark 可安装/启用 DCGM；monitor 会回退到 nvidia-smi"))

    try:
        platform_summary = GPUCollector().detect_platform(config.platform if config else PlatformConfig(), timeout_seconds=3)
        checks.append(_check("platform", "ok", f"profile={platform_summary.get('profile')} source={platform_summary.get('telemetry_source_active')}"))
        if platform_summary.get("telemetry_source_error"):
            checks.append(_check("telemetry_fallback", "warn", str(platform_summary.get("telemetry_source_error")), "DCGM 失败不阻断；会尝试 nvidia-smi"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("platform", "warn", str(exc), "确认 nvidia-smi 可用"))

    checks.extend(check_history_file(config))

    if config is not None:
        host = config.dashboard.host
        port = config.dashboard.port
        token = env_values.get("GPU_MONITOR_DASHBOARD_AUTH_TOKEN") or config.dashboard.auth.token
        if _port_is_open(host, port):
            checks.append(_check("dashboard_port", "ok", f"{host}:{port} accepts TCP"))
        else:
            checks.append(_check("dashboard_port", "warn", f"{host}:{port} is not reachable", f"确认 {service_name} 已启动，或端口未被改动"))
        if not skip_network_checks:
            base_url = f"http://{host}:{port}"
            ok_health, message, payload = _http_json(f"{base_url}/api/health", token=token, timeout_seconds=3)
            checks.append(_check("api_health", "ok" if ok_health else "warn", message, "检查 token、host、port 和服务日志" if not ok_health else ""))
            ok_metrics, metrics_message = _http_status(f"{base_url}/metrics", token=token, timeout_seconds=3)
            checks.append(_check("api_metrics", "ok" if ok_metrics else "warn", metrics_message, "检查 metrics 是否启用或鉴权设置" if not ok_metrics else ""))
            if test_notify:
                ok_notify, notify_message, _ = _http_json(f"{base_url}/api/test-notify", token=token, method="POST", timeout_seconds=8)
                checks.append(_check("test_notify", "ok" if ok_notify else "warn", notify_message, "检查通知通道配置和 token" if not ok_notify else ""))
    return checks


def render_checks(checks: list[DoctorCheck]) -> str:
    icon = {"ok": "OK", "warn": "WARN", "error": "ERROR"}
    lines = []
    for check in checks:
        line = f"[{icon.get(check.status, check.status.upper())}] {check.name}: {check.message}"
        if check.hint:
            line += f"\n    hint: {check.hint}"
        lines.append(line)
    errors = sum(1 for check in checks if check.status == "error")
    warnings = sum(1 for check in checks if check.status == "warn")
    lines.append(f"\nsummary: errors={errors} warnings={warnings}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose monitor installation and runtime health")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to config.yaml")
    parser.add_argument("--env", type=Path, default=Path("/etc/default/gpu-monitor"), help="Path to gpu-monitor env file")
    parser.add_argument("--service-name", default="gpu-monitor-dashboard", help="systemd service name")
    parser.add_argument("--service-file", type=Path, default=Path("/etc/systemd/system/gpu-monitor-dashboard.service"), help="Path to systemd service file")
    parser.add_argument("--skip-network-checks", action="store_true", help="Skip HTTP health/metrics checks")
    parser.add_argument("--skip-systemd-checks", action="store_true", help="Skip systemctl runtime checks")
    parser.add_argument("--test-notify", action="store_true", help="Call /api/test-notify")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    checks = run_doctor(
        config_path=args.config,
        env_path=args.env,
        service_name=args.service_name,
        service_file=args.service_file,
        skip_network_checks=args.skip_network_checks,
        test_notify=args.test_notify,
        skip_systemd_checks=args.skip_systemd_checks,
    )
    if args.json:
        print(json.dumps([asdict(check) for check in checks], ensure_ascii=False, indent=2))
    else:
        print(render_checks(checks))
    return 1 if any(check.status == "error" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
