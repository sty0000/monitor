from __future__ import annotations

import argparse
import getpass
import os
import platform
try:
    import grp
except ImportError:  # pragma: no cover - Windows fallback
    grp = None
import secrets
import shutil
import subprocess
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .collector import CollectorError, GPUCollector
from .config import PlatformConfig
from .config_migrate import EnvEntry, _format_env_value, apply_migration, build_migration_plan, render_migration_preview, write_migration_output
from .doctor import render_checks, run_doctor

DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class InitDetection:
    project_dir: Path
    python_path: str
    user: str
    group: str
    hostname: str
    nvidia_smi_path: str
    dcgmi_path: str
    platform_summary: dict[str, Any]
    recommended_port: int


@dataclass(frozen=True)
class InitOptions:
    instance_name: str
    host: str
    port: int
    notify_enabled: bool
    low_usage_notify_enabled: bool
    token: str
    force: bool = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _detect_user() -> str:
    return getpass.getuser()


def _detect_group() -> str:
    if grp is None:
        return _detect_user()
    try:
        return grp.getgrgid(os.getgid()).gr_name
    except Exception:  # noqa: BLE001
        return _detect_user()


def detect_environment(collector: GPUCollector | None = None, python_path: str | None = None) -> InitDetection:
    project_dir = _repo_root()
    collector = collector or GPUCollector()
    try:
        platform_summary = collector.detect_platform(PlatformConfig(), timeout_seconds=3)
    except (CollectorError, OSError, RuntimeError) as exc:
        platform_summary = {
            "profile": "generic_nvidia",
            "configured_profile": "auto",
            "detected_profile": "generic_nvidia",
            "arch": platform.machine(),
            "os": "",
            "driver_version": "",
            "gpu_inventory": [],
            "dgx_dashboard_present": False,
            "dcgm_available": shutil.which("dcgmi") is not None,
            "telemetry_source_active": "nvidia_smi",
            "telemetry_source_error": str(exc),
        }
    profile = str(platform_summary.get("profile") or platform_summary.get("detected_profile") or "generic_nvidia")
    return InitDetection(
        project_dir=project_dir,
        python_path=python_path or sys.executable,
        user=_detect_user(),
        group=_detect_group(),
        hostname=socket.gethostname(),
        nvidia_smi_path=shutil.which("nvidia-smi") or "",
        dcgmi_path=shutil.which("dcgmi") or "",
        platform_summary=platform_summary,
        recommended_port=8093 if profile == "dgx_spark" else 8090,
    )


def _parse_bool_text(value: str, default: bool) -> bool:
    stripped = value.strip().lower()
    if not stripped:
        return default
    if stripped in {"1", "true", "yes", "y", "on"}:
        return True
    if stripped in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _prompt_text(label: str, default: str) -> str:
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_bool(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} [{suffix}]: ")
    return _parse_bool_text(raw, default)


def build_default_options(detection: InitDetection, token: str | None = None) -> InitOptions:
    return InitOptions(
        instance_name=detection.hostname or "gpu-monitor",
        host="127.0.0.1",
        port=detection.recommended_port,
        notify_enabled=True,
        low_usage_notify_enabled=True,
        token=token or f"monitor-{secrets.token_urlsafe(24)}",
    )


def prompt_options(defaults: InitOptions) -> InitOptions:
    return InitOptions(
        instance_name=_prompt_text("实例名称", defaults.instance_name),
        host=_prompt_text("Dashboard Host", defaults.host),
        port=int(_prompt_text("Dashboard Port", str(defaults.port))),
        notify_enabled=_prompt_bool("是否启用通知", defaults.notify_enabled),
        low_usage_notify_enabled=_prompt_bool("是否启用 Low Usage Notify", defaults.low_usage_notify_enabled),
        token=_prompt_text("Bearer Token", defaults.token),
        force=defaults.force,
    )


def build_config(example_path: Path, detection: InitDetection, options: InitOptions) -> dict[str, Any]:
    with example_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    config.setdefault("monitor", {})["instance_name"] = options.instance_name
    config.setdefault("platform", {})["profile"] = "auto"
    config.setdefault("platform", {})["telemetry_order"] = ["dcgm", "nvidia_smi"]
    dashboard = config.setdefault("dashboard", {})
    dashboard["host"] = options.host
    dashboard["port"] = options.port
    auth = dashboard.setdefault("auth", {})
    auth["enabled"] = True
    auth["token"] = "CHANGE_ME_BEARER_TOKEN"
    notify_control = config.setdefault("notify", {}).setdefault("control", {})
    notify_control["enabled"] = options.notify_enabled
    notify_control["low_usage_enabled"] = options.low_usage_notify_enabled
    return config


def build_env_entries(example_env_path: Path, options: InitOptions) -> list[EnvEntry]:
    values = {
        "GPU_MONITOR_NOTIFY_ENABLED": str(options.notify_enabled).lower(),
        "GPU_MONITOR_LOW_USAGE_NOTIFY_ENABLED": str(options.low_usage_notify_enabled).lower(),
        "GPU_MONITOR_DASHBOARD_AUTH_TOKEN": options.token,
        "GPU_MONITOR_DASHBOARD_HOST": options.host,
        "GPU_MONITOR_DASHBOARD_PORT": str(options.port),
    }
    entries: list[EnvEntry] = []
    for line in example_env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            entries.append((line, None))
            continue
        key = stripped.split("=", 1)[0].strip().removeprefix("export ")
        entries.append((key, values.get(key, stripped.split("=", 1)[1].strip())))
    return entries


def build_service(detection: InitDetection, config_path: Path) -> str:
    return f"""[Unit]
Description=GPU Monitor Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={detection.project_dir}
EnvironmentFile=/etc/default/gpu-monitor
Environment=PYTHONUNBUFFERED=1
Environment=PATH={DEFAULT_PATH}
ExecStart={detection.python_path} -m monitor.dashboard --config {config_path}
Restart=always
RestartSec=5
TimeoutStopSec=20
KillSignal=SIGINT
SuccessExitStatus=143
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false
User={detection.user}
Group={detection.group}

[Install]
WantedBy=multi-user.target
"""


def _write_text(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"output already exists, pass --force to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def _render_env(entries: list[EnvEntry]) -> str:
    lines = [key if value is None else f"{key}={_format_env_value(value)}" for key, value in entries]
    return "\n".join(lines).rstrip() + "\n"


def render_detection(detection: InitDetection) -> str:
    platform_summary = detection.platform_summary
    return "\n".join(
        [
            "GPU Monitor 初始化检测结果：",
            f"- 项目目录: {detection.project_dir}",
            f"- Python: {detection.python_path}",
            f"- 用户/组: {detection.user}/{detection.group}",
            f"- 主机名: {detection.hostname}",
            f"- nvidia-smi: {detection.nvidia_smi_path or 'not found'}",
            f"- dcgmi: {detection.dcgmi_path or 'not found; monitor will fall back to nvidia-smi'}",
            f"- 平台: {platform_summary.get('profile', 'unknown')}",
            f"- 推荐端口: {detection.recommended_port}",
        ]
    )


def generate_files(
    detection: InitDetection,
    options: InitOptions,
    example_path: Path,
    env_example_path: Path,
    output_config: Path,
    output_env: Path,
    output_service: Path,
    dry_run: bool = False,
) -> dict[str, str]:
    config = build_config(example_path, detection, options)
    env_text = _render_env(build_env_entries(env_example_path, options))
    service_text = build_service(detection, output_config.resolve())
    config_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    outputs = {
        str(output_config): config_text,
        str(output_env): env_text,
        str(output_service): service_text,
    }
    if dry_run:
        return outputs
    _write_text(output_config, config_text, options.force)
    _write_text(output_env, env_text, options.force)
    _write_text(output_service, service_text, options.force)
    return outputs


def _backup_existing(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    return backup


def install_systemd_files(
    env_source: Path,
    service_source: Path,
    system_env_path: Path,
    system_service_path: Path,
    run_command=subprocess.run,
    use_sudo: bool = True,
) -> list[str]:
    actions: list[str] = []
    if use_sudo:
        run_command(["sudo", "mkdir", "-p", str(system_env_path.parent), str(system_service_path.parent)], check=True)
        if system_env_path.exists():
            env_backup = system_env_path.with_name(f"{system_env_path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            run_command(["sudo", "cp", "-a", str(system_env_path), str(env_backup)], check=True)
            actions.append(f"backed up {system_env_path} -> {env_backup}")
        if system_service_path.exists():
            service_backup = system_service_path.with_name(f"{system_service_path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            run_command(["sudo", "cp", "-a", str(system_service_path), str(service_backup)], check=True)
            actions.append(f"backed up {system_service_path} -> {service_backup}")
        run_command(["sudo", "cp", str(env_source), str(system_env_path)], check=True)
        actions.append(f"installed {env_source} -> {system_env_path}")
        run_command(["sudo", "cp", str(service_source), str(system_service_path)], check=True)
        actions.append(f"installed {service_source} -> {system_service_path}")
        commands = [
            ["sudo", "systemctl", "daemon-reload"],
            ["sudo", "systemctl", "enable", "--now", "gpu-monitor-dashboard"],
        ]
    else:
        env_backup = _backup_existing(system_env_path)
        if env_backup is not None:
            actions.append(f"backed up {system_env_path} -> {env_backup}")
        service_backup = _backup_existing(system_service_path)
        if service_backup is not None:
            actions.append(f"backed up {system_service_path} -> {service_backup}")
        system_env_path.parent.mkdir(parents=True, exist_ok=True)
        system_service_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(env_source, system_env_path)
        actions.append(f"installed {env_source} -> {system_env_path}")
        shutil.copy2(service_source, system_service_path)
        actions.append(f"installed {service_source} -> {system_service_path}")
        commands = [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", "--now", "gpu-monitor-dashboard"],
        ]
    for command in commands:
        run_command(command, check=True)
        actions.append("ran " + " ".join(command))
    return actions



def uninstall_systemd_files(
    system_env_path: Path,
    system_service_path: Path,
    run_command=subprocess.run,
    use_sudo: bool = True,
    remove_system_files: bool = False,
    dry_run: bool = False,
) -> list[str]:
    actions: list[str] = []
    prefix = ["sudo"] if use_sudo else []
    commands = [
        [*prefix, "systemctl", "stop", "gpu-monitor-dashboard"],
        [*prefix, "systemctl", "disable", "gpu-monitor-dashboard"],
    ]
    for command in commands:
        if dry_run:
            actions.append("would run " + " ".join(command))
        else:
            run_command(command, check=False)
            actions.append("ran " + " ".join(command))

    for target in [system_env_path, system_service_path]:
        if target.exists():
            backup = target.with_name(f"{target.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            if dry_run:
                actions.append(f"would back up {target} -> {backup}")
            elif use_sudo:
                run_command(["sudo", "cp", "-a", str(target), str(backup)], check=True)
                actions.append(f"backed up {target} -> {backup}")
            else:
                shutil.copy2(target, backup)
                actions.append(f"backed up {target} -> {backup}")
            if remove_system_files:
                if dry_run:
                    actions.append(f"would remove {target}")
                elif use_sudo:
                    run_command(["sudo", "rm", "-f", str(target)], check=True)
                    actions.append(f"removed {target}")
                else:
                    target.unlink(missing_ok=True)
                    actions.append(f"removed {target}")
        else:
            actions.append(f"not found {target}")

    daemon_reload = [*prefix, "systemctl", "daemon-reload"]
    if dry_run:
        actions.append("would run " + " ".join(daemon_reload))
    else:
        run_command(daemon_reload, check=True)
        actions.append("ran " + " ".join(daemon_reload))
    return actions


def confirm_systemd_uninstall(system_env_path: Path, system_service_path: Path, remove_system_files: bool) -> bool:
    print("\n即将执行 systemd 卸载/回滚：")
    print("- 运行: systemctl stop gpu-monitor-dashboard")
    print("- 运行: systemctl disable gpu-monitor-dashboard")
    print(f"- 备份环境文件: {system_env_path}")
    print(f"- 备份 service: {system_service_path}")
    if remove_system_files:
        print("- 删除系统 env/service 文件（删除前会备份）")
    else:
        print("- 保留系统 env/service 文件，仅停止并禁用服务")
    print("- 运行: systemctl daemon-reload")
    return _prompt_bool("确认继续", False)


def confirm_systemd_install(system_env_path: Path, system_service_path: Path) -> bool:
    print("\n即将执行 systemd 安装：")
    print(f"- 写入环境文件: {system_env_path}")
    print(f"- 写入 service: {system_service_path}")
    print("- 运行: systemctl daemon-reload")
    print("- 运行: systemctl enable --now gpu-monitor-dashboard")
    print("- 安装后检查: /api/health 与 /metrics")
    return _prompt_bool("确认继续", False)


def render_conda_setup() -> str:
    return "\n".join([
        "# 如果你还没有 monitor 环境，先执行：",
        "conda create -n monitor python=3.10 -y",
        "conda activate monitor",
        "cd path-to-monitor",
        "pip install -U pip",
        "pip install -r requirements.txt",
        "python -m monitor.init_config --check-python-env",
    ])


def check_python_env() -> list[str]:
    checks: list[str] = []
    modules = {"flask": "Flask", "yaml": "PyYAML", "prometheus_client": "prometheus-client"}
    for module_name, package_name in modules.items():
        try:
            __import__(module_name)
        except ImportError:
            checks.append(f"MISSING {package_name}: run pip install -r requirements.txt")
        else:
            checks.append(f"OK {package_name}")
    checks.append(f"Python: {sys.executable}")
    return checks


def run_post_install_checks(config_path: Path, env_path: Path, test_notify: bool = False, wait_seconds: float = 2.0) -> list[Any]:
    if wait_seconds > 0:
        import time

        time.sleep(wait_seconds)
    return run_doctor(config_path=config_path, env_path=env_path, test_notify=test_notify)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize GPU Monitor config, env, and systemd service files")
    parser.add_argument("--non-interactive", action="store_true", help="Use detected/default values without prompts")
    parser.add_argument("--dry-run", action="store_true", help="Print generated content without writing files")
    parser.add_argument("--install-systemd", action="store_true", help="Install generated env/service files and start systemd service")
    parser.add_argument("--uninstall-systemd", action="store_true", help="Stop/disable systemd service and back up system files")
    parser.add_argument("--remove-system-files", action="store_true", help="With --uninstall-systemd, remove env/service files after backing them up")
    parser.add_argument("--force", action="store_true", help="Overwrite output files if they already exist")
    parser.add_argument("--instance-name", default=None, help="monitor.instance_name value")
    parser.add_argument("--host", default=None, help="Dashboard host")
    parser.add_argument("--port", type=int, default=None, help="Dashboard port")
    parser.add_argument("--notify-enabled", choices=["true", "false"], default=None, help="Master notification switch")
    parser.add_argument("--low-usage-notify-enabled", choices=["true", "false"], default=None, help="Low usage alert notification switch")
    parser.add_argument("--token", default=None, help="Bearer token; generated automatically when omitted")
    parser.add_argument("--example", type=Path, default=Path("config.example.yaml"), help="Path to config.example.yaml")
    parser.add_argument("--env-example", type=Path, default=Path("deploy/gpu-monitor.env.example"), help="Path to env example")
    parser.add_argument("--output-config", type=Path, default=Path("config.yaml"), help="Output config path")
    parser.add_argument("--output-env", type=Path, default=Path("gpu-monitor.env.new"), help="Output EnvironmentFile path")
    parser.add_argument("--output-service", type=Path, default=Path("gpu-monitor-dashboard.service.new"), help="Output systemd service path")
    parser.add_argument("--system-env-path", type=Path, default=Path("/etc/default/gpu-monitor"), help="Target systemd EnvironmentFile path")
    parser.add_argument("--system-service-path", type=Path, default=Path("/etc/systemd/system/gpu-monitor-dashboard.service"), help="Target systemd service path")
    parser.add_argument("--post-install-test-notify", action="store_true", help="Call /api/test-notify after --install-systemd")
    parser.add_argument("--print-conda-setup", action="store_true", help="Print recommended Conda setup commands and exit")
    parser.add_argument("--check-python-env", action="store_true", help="Check required Python imports and exit")
    parser.add_argument("--upgrade-config", action="store_true", help="Upgrade an existing config using --example schema and exit")
    parser.add_argument("--upgrade-output", type=Path, default=None, help="With --upgrade-config, write upgraded config to this path")
    parser.add_argument("--upgrade-apply", action="store_true", help="With --upgrade-config, back up and replace --output-config")
    parser.add_argument("--backup-dir", type=Path, default=None, help="With --upgrade-apply, directory for config backup")
    return parser.parse_args()


def _apply_arg_overrides(defaults: InitOptions, args: argparse.Namespace) -> InitOptions:
    return InitOptions(
        instance_name=args.instance_name or defaults.instance_name,
        host=args.host or defaults.host,
        port=args.port or defaults.port,
        notify_enabled=_parse_bool_text(args.notify_enabled or "", defaults.notify_enabled),
        low_usage_notify_enabled=_parse_bool_text(args.low_usage_notify_enabled or "", defaults.low_usage_notify_enabled),
        token=args.token or defaults.token,
        force=args.force,
    )


def main() -> int:
    args = parse_args()
    if args.print_conda_setup:
        print(render_conda_setup())
        return 0
    if args.check_python_env:
        checks = check_python_env()
        print("\n".join(checks))
        return 1 if any(item.startswith("MISSING") for item in checks) else 0
    if args.upgrade_config:
        plan = build_migration_plan(args.example, args.output_config)
        preview = render_migration_preview(plan)
        print(preview, end="")
        if args.dry_run:
            return 0
        if args.upgrade_apply:
            backup_path = apply_migration(plan, args.output_config, backup_dir=args.backup_dir)
            print(f"applied upgraded config: {args.output_config}")
            print(f"backup: {backup_path}")
            return 0
        output_path = args.upgrade_output or args.output_config.with_name(f"{args.output_config.name}.upgraded")
        write_migration_output(plan, output_path, force=args.force)
        print(f"wrote upgraded config: {output_path}")
        return 0
    if args.uninstall_systemd:
        if args.dry_run or args.non_interactive or confirm_systemd_uninstall(args.system_env_path, args.system_service_path, args.remove_system_files):
            actions = uninstall_systemd_files(args.system_env_path, args.system_service_path, remove_system_files=args.remove_system_files, dry_run=args.dry_run)
            print("\n卸载/回滚步骤：")
            for action in actions:
                print(f"- {action}")
        else:
            print("已取消 systemd 卸载。")
        return 0

    detection = detect_environment()
    print(render_detection(detection))
    defaults = _apply_arg_overrides(build_default_options(detection, args.token), args)
    options = defaults if args.non_interactive or args.dry_run else prompt_options(defaults)
    outputs = generate_files(
        detection=detection,
        options=options,
        example_path=args.example,
        env_example_path=args.env_example,
        output_config=args.output_config,
        output_env=args.output_env,
        output_service=args.output_service,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        for filename, content in outputs.items():
            print(f"\n--- {filename} ---\n{content}", end="")
    else:
        print("\n已生成：")
        for filename in outputs:
            print(f"- {filename}")
        if args.install_systemd:
            if args.non_interactive or confirm_systemd_install(args.system_env_path, args.system_service_path):
                actions = install_systemd_files(args.output_env, args.output_service, args.system_env_path, args.system_service_path)
                print("\n已安装 systemd：")
                for action in actions:
                    print(f"- {action}")
                print("\n安装后检查：")
                checks = run_post_install_checks(args.output_config, args.system_env_path, test_notify=args.post_install_test_notify)
                print(render_checks(checks))
            else:
                print("已取消 systemd 安装；生成文件保留在当前目录。")
        else:
            print("\n下一步：")
            print(f"sudo cp {args.output_env} {args.system_env_path}")
            print(f"sudo cp {args.output_service} {args.system_service_path}")
            print("sudo systemctl daemon-reload")
            print("sudo systemctl enable --now gpu-monitor-dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
