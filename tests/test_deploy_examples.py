from __future__ import annotations

import json
from pathlib import Path

import yaml


def test_prometheus_scrape_example_is_valid_yaml() -> None:
    path = Path("deploy/prometheus.scrape.example.yml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data["scrape_configs"][0]["job_name"] == "gpu-monitor"
    assert data["scrape_configs"][0]["metrics_path"] == "/metrics"


def test_grafana_dashboard_example_is_valid_json() -> None:
    path = Path("deploy/grafana-dashboard.example.json")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["title"] == "GPU Monitor Production"
    expressions = [target["expr"] for panel in data["panels"] for target in panel.get("targets", [])]
    assert "gpu_monitor_gpu_utilization_percent" in expressions
    assert "gpu_monitor_gpu_temperature_celsius" in expressions
    assert "gpu_monitor_history_points" in expressions
    assert "gpu_monitor_gpu_sm_clock_mhz" in expressions
    assert "gpu_monitor_gpu_ecc_error_count" in expressions


def test_prometheus_alert_rules_example_is_valid_yaml() -> None:
    path = Path("deploy/prometheus.alerts.example.yml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    alerts = {rule["alert"] for group in data["groups"] for rule in group["rules"] if "alert" in rule}
    assert "GpuMonitorRuntimeDown" in alerts
    assert "GpuHighTemperature" in alerts
    assert "GpuMonitorNotifierUnhealthy" in alerts


def test_prometheus_recording_rules_example_is_valid_yaml() -> None:
    path = Path("deploy/prometheus.recording.example.yml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    records = {rule["record"] for group in data["groups"] for rule in group["rules"] if "record" in rule}
    assert "gpu_monitor:gpu_utilization_avg5m" in records
    assert "gpu_monitor:instance_error_count" in records


def test_grafana_provisioning_datasource_example_is_valid_yaml() -> None:
    path = Path("deploy/grafana-provisioning-datasource.example.yml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data["apiVersion"] == 1
    assert data["datasources"][0]["type"] == "prometheus"
