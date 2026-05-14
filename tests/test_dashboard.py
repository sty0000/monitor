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

