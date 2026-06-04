from pathlib import Path

from monitor.dashboard import create_app
from monitor.runtime_service import MonitorRuntimeService


def _write_config(path: Path, require_auth_for_read: bool = False) -> None:
    path.write_text(
        f"""
dashboard:
  auth:
    enabled: true
    token: "secret-token"
    require_auth_for_read: {str(require_auth_for_read).lower()}
""",
        encoding="utf-8",
    )


def test_dashboard_write_requires_bearer_token(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    app = create_app(runtime)
    client = app.test_client()
    response = client.post("/api/notify", json={"enabled": True})
    assert response.status_code == 401
    ok = client.post("/api/notify", json={"enabled": True}, headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


def test_dashboard_read_can_be_open_by_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, require_auth_for_read=False)
    runtime = MonitorRuntimeService(config_path)
    app = create_app(runtime)
    client = app.test_client()
    response = client.get("/api/health")
    assert response.status_code == 200



def test_dashboard_index_contains_instance_name_placeholder(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    app = create_app(runtime)
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="instanceName"' in html
    assert 'pageTitle' in html


def test_dashboard_can_mute_gpu_error_alert(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    app = create_app(runtime)
    client = app.test_client()

    response = client.post(
        "/api/gpu-error-mute",
        json={"gpu_id": 3, "muted": True},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert response.get_json()["muted_gpu_error_ids"] == [3]

    status = client.get("/api/status")
    assert status.get_json()["muted_gpu_error_ids"] == [3]


def test_dashboard_can_toggle_low_usage_notify(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    app = create_app(runtime)
    client = app.test_client()

    response = client.post(
        "/api/low-usage-notify",
        json={"enabled": False},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert response.get_json()["low_usage_notify_enabled"] is False

    status = client.get("/api/status")
    assert status.get_json()["low_usage_notify_enabled"] is False


def test_dashboard_contains_dgx_spark_cards(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    app = create_app(runtime)
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert "System / Unified Memory" in html
    assert "gpuMemHeader" in html
    assert "Top Processes" in html
    assert "btnLowUsageDisable" in html
