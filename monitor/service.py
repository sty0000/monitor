from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask

from .config import AppConfig, load_config
from .logging_utils import configure_logging
from .runtime_service import MonitorRuntimeService


def load_runtime(config_path: Path) -> tuple[AppConfig, MonitorRuntimeService]:
    config = load_config(config_path)
    configure_logging(config.logging)
    return config, MonitorRuntimeService(config_path)


def collect_once(config_path: Path) -> dict[str, Any]:
    config, runtime = load_runtime(config_path)
    return runtime.collector.collect_sample(config.monitor, config.platform)


def print_once(config_path: Path) -> None:
    print(json.dumps(collect_once(config_path), ensure_ascii=False, indent=2))


def wait_until_stopped(runtime: MonitorRuntimeService) -> None:
    try:
        while True:
            runtime.stop_event.wait(1)
            if runtime.stop_event.is_set():
                break
    except KeyboardInterrupt:
        pass


def run_agent(config_path: Path) -> int:
    _, runtime = load_runtime(config_path)
    runtime.start()
    try:
        wait_until_stopped(runtime)
    finally:
        runtime.stop()
    return 0


def build_dashboard_app(config_path: Path) -> tuple[AppConfig, MonitorRuntimeService, Flask]:
    from .dashboard import create_app

    config, runtime = load_runtime(config_path)
    return config, runtime, create_app(runtime)


def run_dashboard(config_path: Path, host: str | None = None, port: int | None = None) -> int:
    config, runtime, app = build_dashboard_app(config_path)
    runtime.start()
    bind_host = host or config.dashboard.host
    bind_port = port or config.dashboard.port
    try:
        app.run(host=bind_host, port=bind_port, debug=False)
    finally:
        runtime.stop()
    return 0
