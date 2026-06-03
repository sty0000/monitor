from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

from .config import load_config
from .logging_utils import configure_logging
from .runtime_service import MonitorRuntimeService


def _html_page() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GPU Monitor Dashboard</title>
  <style>
    body { font-family: sans-serif; margin: 24px; background: #0f172a; color: #e2e8f0; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .card { background: #111827; border: 1px solid #334155; border-radius: 10px; padding: 16px; min-width: 260px; }
    button { margin-right: 8px; margin-bottom: 8px; padding: 8px 12px; }
    table { border-collapse: collapse; width: 100%; }
    td, th { border: 1px solid #334155; padding: 8px; text-align: left; }
    .ok { color: #22c55e; }
    .warn { color: #f59e0b; }
    .bad { color: #ef4444; }
    pre { white-space: pre-wrap; word-break: break-word; }
    input { padding: 8px; min-width: 320px; margin-right: 8px; }
  </style>
</head>
<body>
<h1 id="pageTitle">GPU Monitor Dashboard</h1>
<p>设备名：<strong id="instanceName">-</strong></p>
<p>请在下方填入 Bearer Token（如果启用鉴权）。浏览器不会自动保存。</p>
<div>
  <input id="token" type="password" placeholder="Bearer Token" />
  <button id="saveToken">应用 Token</button>
</div>

<div class="row">
  <div class="card"><h3>Monitor State</h3><div id="monitorState">-</div></div>
  <div class="card"><h3>Notify</h3><div id="notifyState">-</div></div>
  <div class="card"><h3>Intervals</h3><div id="intervalState">-</div></div>
  <div class="card"><h3>Channels</h3><div id="routeState">-</div></div>
  <div class="card"><h3>Platform</h3><pre id="platformBody">-</pre></div>
  <div class="card"><h3>System / Unified Memory</h3><div id="memoryBody">-</div></div>
</div>

<div style="margin: 16px 0;">
  <button id="btnEnable">Enable Notify</button>
  <button id="btnDisable">Disable Notify</button>
  <button id="btnTest">Send Test</button>
  <button id="btnReload">Reload Config</button>
</div>

<div class="card">
  <h3>GPU Snapshot</h3>
  <table>
    <thead><tr><th>GPU</th><th>Util %</th><th id="gpuMemHeader">Mem MB</th><th>Power W</th><th>Temp C</th><th>PIDs</th><th>Device Error</th><th>GPU Error Alert</th></tr></thead>
    <tbody id="gpuBody"></tbody>
  </table>
</div>

<div class="card" style="margin-top: 16px;">
  <h3>Top Processes</h3>
  <div style="margin-bottom: 8px;">
    <button id="sortCpu">Sort by CPU</button>
    <button id="sortMem">Sort by Memory</button>
    <span id="processSortState">CPU</span>
  </div>
  <table>
    <thead><tr><th>PID</th><th>User</th><th>CPU %</th><th>Mem %</th><th>RSS MB</th><th>Command</th><th>Args</th></tr></thead>
    <tbody id="processBody"></tbody>
  </table>
</div>

<div class="card" style="margin-top: 16px;"><h3>Health</h3><pre id="healthBody">-</pre></div>
<div class="card" style="margin-top: 16px;"><h3>Recent Events</h3><pre id="events">-</pre></div>

<script>
var bearerToken = '';
var processSortKey = 'cpu';

function buildHeaders() {
  var headers = { 'Content-Type': 'application/json' };
  if (bearerToken) {
    headers.Authorization = 'Bearer ' + bearerToken;
  }
  return headers;
}

function api(url, method, body) {
  if (!method) {
    method = 'GET';
  }
  return fetch(url, {
    method: method,
    headers: buildHeaders(),
    body: body ? JSON.stringify(body) : null,
  }).then(function (resp) {
    if (!resp.ok) {
      return resp.text().catch(function () { return ''; }).then(function (text) {
        throw new Error('HTTP ' + resp.status + ': ' + text);
      });
    }
    var type = resp.headers.get('content-type') || '';
    if (type.indexOf('application/json') >= 0) {
      return resp.json();
    }
    return resp.text();
  });
}

function fmtEvents(events) {
  return events.map(function (event) {
    var extra = event.extra ? '\\n' + JSON.stringify(event.extra, null, 2) : '';
    return '[' + event.ts + '] ' + event.kind + ': ' + event.message + extra;
  }).join('\\n\\n');
}

function render(status, health) {
  var instanceName = ((status.config_summary || {}).monitor || {}).instance_name || '-';
  document.getElementById('instanceName').textContent = instanceName;
  document.getElementById('pageTitle').textContent = 'GPU Monitor Dashboard - ' + instanceName;
  document.title = 'GPU Monitor Dashboard - ' + instanceName;
  var state = status.monitor_state || '-';
  var cls = state === 'ACTIVE' ? 'ok' : ((state.indexOf('ALERT') >= 0 || state === 'ERROR') ? 'bad' : 'warn');
  document.getElementById('monitorState').innerHTML = '<span class="' + cls + '">' + state + '</span><div>' + (status.reason || '') + '</div>';
  document.getElementById('notifyState').innerHTML = status.notify_enabled ? '<span class="ok">ON</span>' : '<span class="warn">OFF</span>';
  document.getElementById('intervalState').textContent = status.interval_seconds + 's / cooldown ' + status.cooldown_minutes + 'm / global ' + status.min_interval_minutes + 'm';
  document.getElementById('routeState').textContent = (status.notifier_order_active || []).join(' -> ') || '(none)';

  var platformSummary = status.platform_summary || {};
  var systemMemory = (status.sample && status.sample.system_memory) || {};
  var memoryLabel = platformSummary.profile === 'dgx_spark' ? 'GPU/Unified Mem MB' : 'Mem MB';
  document.getElementById('gpuMemHeader').textContent = memoryLabel;
  document.getElementById('platformBody').textContent = JSON.stringify(platformSummary, null, 2);
  document.getElementById('memoryBody').innerHTML = systemMemory.ok ? ('used ' + systemMemory.used_mb + 'MB / total ' + systemMemory.total_mb + 'MB (' + systemMemory.used_percent + '%), available ' + systemMemory.available_mb + 'MB') : (systemMemory.error || 'N/A');

  var mutedGpuIds = status.muted_gpu_error_ids || [];
  var gpus = (status.sample && status.sample.gpus) || [];
  function fmtValue(value) {
    return value === null || value === undefined ? 'N/A' : value;
  }
  var rows = gpus.map(function (gpu) {
    var muted = mutedGpuIds.indexOf(gpu.index) >= 0;
    var errorText = gpu.device_error || '';
    var buttonText = muted ? 'Enable GPU error alert' : 'Mute GPU error alert';
    var button = '<button data-gpu-id="' + gpu.index + '" data-muted="' + muted + '" class="gpuMuteBtn">' + buttonText + '</button>';
    return '<tr><td>' + gpu.index + '</td><td>' + fmtValue(gpu.utilization_gpu) + '</td><td>' + fmtValue(gpu.memory_used_mb) + '</td><td>' + fmtValue(gpu.power_draw_w) + '</td><td>' + fmtValue(gpu.temperature_c) + '</td><td>' + ((gpu.compute_pids || []).join(',')) + '</td><td class="' + (errorText ? 'bad' : 'ok') + '">' + (errorText || 'OK') + '</td><td>' + button + '</td></tr>';
  }).join('');
  document.getElementById('gpuBody').innerHTML = rows || '<tr><td colspan="8">No data</td></tr>';
  Array.prototype.forEach.call(document.getElementsByClassName('gpuMuteBtn'), function (button) {
    button.onclick = function () {
      var gpuId = Number(button.getAttribute('data-gpu-id'));
      var currentlyMuted = button.getAttribute('data-muted') === 'true';
      api('/api/gpu-error-mute', 'POST', { gpu_id: gpuId, muted: !currentlyMuted }).then(refresh);
    };
  });
  var processUsage = (status.sample && status.sample.process_usage) || {};
  var processRows = (processSortKey === 'memory' ? processUsage.top_memory : processUsage.top_cpu) || [];
  document.getElementById('processSortState').textContent = processSortKey === 'memory' ? 'Memory' : 'CPU';
  document.getElementById('processBody').innerHTML = processRows.map(function (proc) {
    return '<tr><td>' + proc.pid + '</td><td>' + proc.user + '</td><td>' + fmtValue(proc.cpu_percent) + '</td><td>' + fmtValue(proc.memory_percent) + '</td><td>' + fmtValue(proc.rss_mb) + '</td><td>' + proc.command + '</td><td>' + proc.args + '</td></tr>';
  }).join('') || '<tr><td colspan="7">' + (processUsage.error || 'No data') + '</td></tr>';

  document.getElementById('healthBody').textContent = JSON.stringify(health, null, 2);
  document.getElementById('events').textContent = fmtEvents(status.events || []);
}

function refresh() {
  return Promise.all([api('/api/status'), api('/api/health')])
    .then(function (results) {
      render(results[0], results[1]);
    })
    .catch(function (err) {
      document.getElementById('events').textContent = '拉取状态失败: ' + err.message;
    });
}

document.getElementById('saveToken').onclick = function () {
  bearerToken = document.getElementById('token').value.trim();
  refresh();
};
document.getElementById('btnEnable').onclick = function () { api('/api/notify', 'POST', { enabled: true }).then(refresh); };
document.getElementById('btnDisable').onclick = function () { api('/api/notify', 'POST', { enabled: false }).then(refresh); };
document.getElementById('btnTest').onclick = function () { api('/api/test-notify', 'POST', {}).then(refresh); };
document.getElementById('btnReload').onclick = function () { api('/api/reload-config', 'POST', {}).then(refresh); };
document.getElementById('sortCpu').onclick = function () { processSortKey = 'cpu'; refresh(); };
document.getElementById('sortMem').onclick = function () { processSortKey = 'memory'; refresh(); };

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def _ensure_auth(runtime: MonitorRuntimeService, write: bool) -> Response | None:
    if runtime.is_authorized(request.headers.get("Authorization"), write=write):
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def create_app(runtime: MonitorRuntimeService) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        return _html_page()

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status=204)

    @app.get("/api/status")
    def status() -> Any:
        auth_error = _ensure_auth(runtime, write=False)
        if auth_error is not None:
            return auth_error
        return jsonify(runtime.get_status())

    @app.get("/api/health")
    def health() -> Any:
        auth_error = _ensure_auth(runtime, write=False)
        if auth_error is not None:
            return auth_error
        return jsonify(runtime.get_health())

    @app.get("/metrics")
    def metrics() -> Any:
        auth_error = _ensure_auth(runtime, write=False)
        if auth_error is not None:
            return auth_error
        return Response(runtime.get_metrics_payload(), mimetype="text/plain; version=0.0.4; charset=utf-8")

    @app.post("/api/notify")
    def set_notify() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled", True))
        runtime.set_notify_enabled(enabled)
        return jsonify({"ok": True, "notify_enabled": enabled})

    @app.post("/api/gpu-error-mute")
    def set_gpu_error_mute() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            gpu_id = int(payload["gpu_id"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": "gpu_id is required"}), 400
        muted = bool(payload.get("muted", True))
        muted_gpu_error_ids = runtime.set_gpu_error_muted(gpu_id, muted)
        return jsonify({"ok": True, "gpu_id": gpu_id, "muted": muted, "muted_gpu_error_ids": muted_gpu_error_ids})

    @app.post("/api/test-notify")
    def test_notify() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        payload = request.get_json(silent=True) or {}
        try:
            sent = runtime.send_test_notification(payload.get("channel"))
            return jsonify({"ok": True, "sent": sent, "channel": payload.get("channel")})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/reload-config")
    def reload_config() -> Any:
        auth_error = _ensure_auth(runtime, write=True)
        if auth_error is not None:
            return auth_error
        result = runtime.reload_config()
        return jsonify(result)

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPU monitor dashboard web UI")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to YAML config file")
    parser.add_argument("--host", default=None, help="Bind host (overrides config)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (overrides config)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config.logging)
    runtime = MonitorRuntimeService(args.config)
    runtime.start()
    app = create_app(runtime)
    host = args.host or config.dashboard.host
    port = args.port or config.dashboard.port
    try:
        app.run(host=host, port=port, debug=False)
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



