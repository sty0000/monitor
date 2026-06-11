from __future__ import annotations

from pathlib import Path

from monitor.dashboard import create_app
from monitor.runtime_service import MonitorRuntimeService


def _write_config(path: Path) -> None:
    path.write_text("dashboard:\n  auth:\n    enabled: false\n", encoding="utf-8")


def test_status_health_history_schema_and_metrics(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime._append_history({"timestamp": "t", "gpus": []}, "ACTIVE", "ok")
    client = create_app(runtime).test_client()

    status = client.get("/api/status").get_json()
    assert {"timestamp", "version", "monitor_state", "sample", "events", "config_summary", "silenced_alerts_permanent"}.issubset(status)

    health = client.get("/api/health").get_json()
    assert {"ok", "ready", "version", "monitor_state", "consecutive_failures", "last_error", "uptime_seconds", "runtime_thread_alive", "degraded_domains", "collector_errors", "notifier_health", "config_warnings"}.issubset(health)

    history = client.get("/api/history").get_json()
    assert {"ok", "points", "events"}.issubset(history)

    metrics = client.get("/metrics").get_data(as_text=True)
    assert "gpu_monitor_version_info" in metrics
    required_metrics = [
        "gpu_monitor_up",
        "gpu_monitor_state_code",
        "gpu_monitor_version_info",
        "gpu_monitor_collection_errors_total",
        "gpu_monitor_history_points",
        "gpu_monitor_acknowledged_alerts",
        "gpu_monitor_silenced_alerts",
        "gpu_monitor_notifier_health",
        "gpu_monitor_gpu_utilization_percent",
        "gpu_monitor_gpu_memory_used_mb",
        "gpu_monitor_gpu_power_draw_watts",
        "gpu_monitor_gpu_temperature_celsius",
        "gpu_monitor_gpu_device_error",
    ]
    for metric_name in required_metrics:
        assert metric_name in metrics


def test_metrics_disabled_returns_explicit_disabled_payload(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: false
metrics:
  enabled: false
""",
        encoding="utf-8",
    )
    runtime = MonitorRuntimeService(config_path)
    client = create_app(runtime).test_client()

    metrics = client.get("/metrics").get_data(as_text=True)

    assert "gpu_monitor_metrics disabled by config" in metrics



def test_history_api_supports_bucketed_aggregation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime._append_history({"timestamp": "2026-01-01T00:00:00+00:00", "gpus": [{"index": 0, "utilization_gpu": 10}]}, "ACTIVE", "ok")
    runtime._append_history({"timestamp": "2026-01-01T00:00:30+00:00", "gpus": [{"index": 0, "utilization_gpu": 30}]}, "ACTIVE", "ok")
    client = create_app(runtime).test_client()

    response = client.get("/api/history?limit=10&gpu=0&bucket=60&metric=utilization_gpu&agg=avg")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["aggregate"][0]["value"] == 20


def test_history_api_rejects_invalid_aggregation_params(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    client = create_app(runtime).test_client()

    response = client.get("/api/history?bucket=bad")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False

    response = client.get("/api/history?bucket=60&agg=median")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False
