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
    assert "@media (max-width: 720px)" in html






def test_dashboard_uses_unified_memory_fallback_for_dgx_spark(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime.get_status = lambda: {
        "config_summary": {"monitor": {"instance_name": "test"}},
        "version": "test",
        "monitor_state": "ACTIVE",
        "active_alert": "",
        "reason": "ok",
        "notify_enabled": False,
        "low_usage_notify_enabled": False,
        "interval_seconds": 15,
        "cooldown_minutes": 30,
        "min_interval_minutes": 3,
        "notifier_order_active": [],
        "acknowledged_alerts": [],
        "silenced_alerts_until": {},
        "silenced_alerts_permanent": [],
        "muted_gpu_error_ids": [],
        "notifier_health": {},
        "events": [],
        "sample": {
            "platform_summary": {"profile": "dgx_spark"},
            "system_memory": {
                "ok": True,
                "used_mb": 5941.1,
                "total_mb": 124545.9,
                "used_percent": 4.8,
                "available_mb": 118604.8,
            },
            "gpus": [
                {
                    "index": 0,
                    "utilization_gpu": 0,
                    "memory_used_mb": None,
                    "power_draw_w": 5.28,
                    "temperature_c": 40,
                    "compute_pids": [],
                    "device_error": None,
                }
            ],
            "process_usage": {"ok": True, "top_cpu": [], "top_memory": []},
        },
    }
    runtime.get_history = lambda **kwargs: {"points": [], "events": []}
    app = create_app(runtime)
    html = app.test_client().get("/").get_data(as_text=True)

    assert "GPU/Unified Mem MB" in html
    assert "formatGpuMemory(gpu)" in html
    assert "platformSummary = ((status.sample || {}).platform_summary) || status.platform_summary || {};" in html
    assert "systemMemory.used_mb + ' / ' + systemMemory.total_mb + ' unified'" in html
    assert "formatGpuMemory(gpu)" in html

def test_dashboard_inline_script_escapes_newlines(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    html = create_app(runtime).test_client().get("/").get_data(as_text=True)

    assert "join('\\n')" in html
    assert "validation:\\n" in html
    assert "Apply result:\\n" in html
    assert "rolled_back=true.\\n" in html
    assert "Ack: " in html and "\\nTemp: " in html and "\\nPermanent: " in html
    assert "payload === undefined ? '' : '\\n'" in html

def test_dashboard_first_screen_prioritizes_status_and_readonly_tables(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    html = create_app(runtime).test_client().get("/").get_data(as_text=True)

    overview_pos = html.index('id="overviewRow"')
    gpu_pos = html.index("GPU Snapshot")
    process_pos = html.index("Top Processes")
    advanced_pos = html.index("Advanced controls and diagnostics")

    assert "Active Alert" in html
    assert "GPU Workload" in html
    assert overview_pos < gpu_pos < process_pos < advanced_pos
    assert 'class="table-wrap"' in html
    assert 'id="btnReload"' in html
    assert 'id="configEditorCard"' in html
    assert '首屏只看 GPU 是否在工作' not in html








def test_dashboard_history_trend_lightweight_enhancements(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    html = create_app(runtime).test_client().get("/").get_data(as_text=True)

    assert 'class="chart-scroll"' in html
    assert 'id="historyGpuSelect"' in html
    assert 'width="960" height="240"' in html
    assert "collectHistoryGpuIds" in html
    assert "syncHistoryGpuSelect" in html
    assert "historyScale" in html
    assert "[0, 25, 50, 75, 100]" in html
    assert "Chart.js" not in html
    assert "echarts" not in html.lower()

def test_dashboard_exposes_history_api_and_chart(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime._append_history({"timestamp": "t", "gpus": [{"index": 0, "utilization_gpu": 1, "temperature_c": 2}]}, "ACTIVE", "ok")
    app = create_app(runtime)
    client = app.test_client()

    response = client.get("/api/history?limit=1")
    assert response.status_code == 200
    assert response.get_json()["points"][0]["state"] == "ACTIVE"

    html = client.get("/").get_data(as_text=True)
    assert "historyChart" in html
    assert "Alert Timeline" in html



def test_dashboard_can_acknowledge_current_alert(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime.state.active_alert = "LOW_USAGE_ALERT"
    app = create_app(runtime)
    client = app.test_client()

    response = client.post("/api/acknowledge-alert", json={}, headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200
    assert response.get_json()["alert_key"] == "LOW_USAGE_ALERT"
    status = client.get("/api/status")
    assert "LOW_USAGE_ALERT" in status.get_json()["acknowledged_alerts"]

    silence = client.post("/api/acknowledge-alert", json={"alert_key": "GPU_ERROR_ALERT", "silence_minutes": 5}, headers={"Authorization": "Bearer secret-token"})
    assert silence.status_code == 200
    status = client.get("/api/status").get_json()
    assert "GPU_ERROR_ALERT" in status["silenced_alerts_until"]


def test_dashboard_alert_silence_api_and_buttons(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("dashboard:\n  auth:\n    enabled: false\n", encoding="utf-8")
    runtime = MonitorRuntimeService(config_path)
    runtime.state.active_alert = "GPU_ERROR_ALERT"
    app = create_app(runtime)
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert "btnSilenceToday" in html
    assert "btnSilencePermanent" in html
    assert "btnClearSilence" in html
    assert "Control Result" in html
    assert "Acknowledge Current Alert" in html
    assert "Clear Ack/Silence" in html
    assert "runAction('Silence current alert for 1h'" in html

    response = client.post("/api/alert-silence", json={"mode": "permanent"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "GPU_ERROR_ALERT" in payload["silenced_alerts_permanent"]


def test_dashboard_history_filters_query_params(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime._append_history({"timestamp": "2026-01-01T00:00:00+00:00", "gpus": [{"index": 0}]}, "ACTIVE", "ok")
    client = create_app(runtime).test_client()

    response = client.get("/api/history?limit=10&gpu=0&state=ACTIVE")

    assert response.status_code == 200
    assert response.get_json()["points"][0]["gpus"][0]["index"] == 0


def test_all_dashboard_write_apis_require_bearer_token(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime.state.active_alert = "LOW_USAGE_ALERT"
    client = create_app(runtime).test_client()

    endpoints = [
        ("/api/notify", {"enabled": True}),
        ("/api/low-usage-notify", {"enabled": True}),
        ("/api/gpu-error-mute", {"gpu_id": 0, "muted": True}),
        ("/api/test-notify", {}),
        ("/api/acknowledge-alert", {}),
        ("/api/alert-silence", {"mode": "1h"}),
        ("/api/reload-config", {}),
        ("/api/config/preview", {"updates": {"threshold.usage_percent": 30}}),
        ("/api/config/apply", {"updates": {"threshold.usage_percent": 30}}),
    ]
    for endpoint, payload in endpoints:
        assert client.post(endpoint, json=payload).status_code == 401
    assert client.get("/api/config/editable").status_code == 401


def test_dashboard_read_requires_token_when_configured(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, require_auth_for_read=True)
    client = create_app(MonitorRuntimeService(config_path)).test_client()

    assert client.get("/api/health").status_code == 401
    ok = client.get("/api/health", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200



def test_dashboard_health_exposes_p2_diagnostics(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    runtime.latest_sample = {"timestamp": "2026-01-01T00:00:00+00:00", "collector_errors": {"compute_apps": "boom"}}
    client = create_app(runtime).test_client()

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert "uptime_seconds" in payload
    assert payload["ready"] is False
    assert payload["runtime_thread_alive"] is False
    assert "collector" in payload["degraded_domains"]
    assert payload["collector_errors"] == {"compute_apps": "boom"}
    assert "config_warnings" in payload


def test_config_editable_api_exposes_safe_whitelist(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    client = create_app(MonitorRuntimeService(config_path)).test_client()

    response = client.get("/api/config/editable", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    payload = response.get_json()
    paths = {field["path"] for field in payload["fields"]}
    assert "threshold.usage_percent" in paths
    assert "dashboard.auth.token" not in paths


def test_dashboard_contains_config_editor_ui(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    client = create_app(MonitorRuntimeService(config_path)).test_client()

    html = client.get("/").get_data(as_text=True)

    assert 'id="configEditorCard"' in html
    assert 'id="configEditorFields"' in html
    assert 'id="btnConfigPreview"' in html
    assert 'id="btnConfigApply" disabled' in html
    assert 'id="configPreviewBody"' in html
    assert "function loadConfigEditor" in html
    assert "function previewConfigChange" in html
    assert "function applyConfigChange" in html
    assert "Preview must pass before apply" in html
    assert "backup_path" in html
    assert "rolled_back=true" in html
    assert "/api/config/editable" in html
    assert "/api/config/preview" in html
    assert "/api/config/apply" in html
    assert "dashboard.auth.token" not in html
    assert "notifier secrets" in html
    assert "systemd paths" in html


def test_dashboard_config_editor_supports_field_types(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    html = create_app(MonitorRuntimeService(config_path)).test_client().get("/").get_data(as_text=True)

    assert "typeName === 'boolean'" in html
    assert "typeName === 'integer'" in html
    assert "typeName === 'integer|null'" in html
    assert "typeName === 'number'" in html
    assert "typeName === 'list[string]'" in html
    assert '<select id="' in html


def test_config_preview_validates_without_writing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    before = config_path.read_text(encoding="utf-8")
    client = create_app(MonitorRuntimeService(config_path)).test_client()

    response = client.post(
        "/api/config/preview",
        json={"updates": {"threshold.usage_percent": 33}},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "threshold" in payload["diff"]
    assert config_path.read_text(encoding="utf-8") == before


def test_config_preview_rejects_non_whitelisted_field(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    client = create_app(MonitorRuntimeService(config_path)).test_client()

    response = client.post(
        "/api/config/preview",
        json={"updates": {"dashboard.auth.token": "leak"}},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 400
    assert "not editable" in response.get_json()["error"]


def test_config_apply_backs_up_writes_and_reloads(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    runtime = MonitorRuntimeService(config_path)
    client = create_app(runtime).test_client()

    response = client.post(
        "/api/config/apply",
        json={"updates": {"threshold.usage_percent": 44}},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert Path(payload["backup_path"]).exists()
    assert runtime.config.threshold.usage_percent == 44
    assert "usage_percent: 44" in config_path.read_text(encoding="utf-8")


def test_config_apply_rolls_back_when_reload_fails(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    before = config_path.read_text(encoding="utf-8")
    runtime = MonitorRuntimeService(config_path)

    def fail_reload():
        raise RuntimeError("reload boom")

    monkeypatch.setattr(runtime, "reload_config", fail_reload)
    client = create_app(runtime).test_client()

    response = client.post(
        "/api/config/apply",
        json={"updates": {"threshold.usage_percent": 55}},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["rolled_back"] is True
    assert config_path.read_text(encoding="utf-8") == before
