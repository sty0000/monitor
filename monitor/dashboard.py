from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

from .config import load_config
from .logging_utils import configure_logging
from .runtime_service import MonitorRuntimeService


def _html_page() -> str:
    return """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\" /><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" /><title>GPU Monitor Dashboard</title><style>body{font-family:sans-serif;margin:24px;background:#0f172a;color:#e2e8f0}.row{display:flex;gap:16px;flex-wrap:wrap}.card{background:#111827;border:1px solid #334155;border-radius:10px;padding:16px;min-width:260px}button{margin-right:8px;margin-bottom:8px;padding:8px 12px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #334155;padding:8px;text-align:left}.ok{color:#22c55e}.warn{color:#f59e0b}.bad{color:#ef4444}pre{white-space:pre-wrap;word-break:break-word}input{padding:8px;min-width:320px;margin-right:8px}</style></head><body><h1>GPU Monitor Dashboard</h1><p>请在下方填入 Bearer Token（如果启用鉴权）。浏览器不会自动保存。</p><div><input id=\"token\" type=\"password\" placeholder=\"Bearer Token\" /><button id=\"saveToken\">应用 Token</button></div><div class=\"row\"><div class=\"card\"><h3>Monitor State</h3><div id=\"monitorState\">-</div></div><div class=\"card\"><h3>Notify</h3><div id=\"notifyState\">-</div></div><div class=\"card\"><h3>Intervals</h3><div id=\"intervalState\">-</div></div><div class=\"card\"><h3>Channels</h3><div id=\"routeState\">-</div></div></div><div style=\"margin:16px 0;\"><button id=\"btnEnable\">Enable Notify</button><button id=\"btnDisable\">Disable Notify</button><button id=\"btnTest\">Send Test</button><button id=\"btnReload\">Reload Config</button></div><div class=\"card\"><h3>GPU Snapshot</h3><table><thead><tr><th>GPU</th><th>Util %</th><th>Mem MB</th><th>Power W</th><th>Temp C</th><th>PIDs</th></tr></thead><tbody id=\"gpuBody\"></tbody></table></div><div class=\"card\" style=\"margin-top:16px;\"><h3>Health</h3><pre id=\"healthBody\">-</pre></div><div class=\"card\" style=\"margin-top:16px;\"><h3>Recent Events</h3><pre id=\"events\">-</pre></div><script>let bearerToken='';function headers(){const value=bearerToken?{'Authorization':`Bearer ${bearerToken}`}:{ };return{'Content-Type':'application/json',...value}}async function api(url,method='GET',body=null){const resp=await fetch(url,{method,headers:headers(),body:body?JSON.stringify(body):null});if(!resp.ok){let text='';try{text=await resp.text()}catch(_){}throw new Error(`HTTP ${resp.status}: ${text}`)}const type=resp.headers.get('content-type')||'';if(type.includes('application/json')){return await resp.json()}return await resp.text()}function fmtEvents(events){return events.map(e=>{const extra=e.extra?`\n${JSON.stringify(e.extra,null,2)}`:'';return`[${e.ts}] ${e.kind}: ${e.message}${extra}`}).join('\n\n')}function render(status,health){const state=status.monitor_state||'-';const cls=state==='ACTIVE'?'ok':(state.includes('ALERT')||state==='ERROR')?'bad':'warn';document.getElementById('monitorState').innerHTML=`<span class=\"${cls}\">${state}</span><div>${status.reason||''}</div>`;document.getElementById('notifyState').innerHTML=status.notify_enabled?'<span class=\"ok\">ON</span>':'<span class=\"warn\">OFF</span>';document.getElementById('intervalState').textContent=`${status.interval_seconds}s / cooldown ${status.cooldown_minutes}m / global ${status.min_interval_minutes}m`;document.getElementById('routeState').textContent=(status.notifier_order_active||[]).join(' -> ')||'(none)';const gpus=(status.sample&&status.sample.gpus)||[];const rows=gpus.map(g=>`<tr><td>${g.index}</td><td>${g.utilization_gpu}</td><td>${g.memory_used_mb}</td><td>${g.power_draw_w}</td><td>${g.temperature_c}</td><td>${(g.compute_pids||[]).join(',')}</td></tr>`).join('');document.getElementById('gpuBody').innerHTML=rows||'<tr><td colspan="6">无数据</td></tr>';document.getElementById('healthBody').textContent=JSON.stringify(health,null,2);document.getElementById('events').textContent=fmtEvents(status.events||[])}async function refresh(){try{const[status,health]=await Promise.all([api('/api/status'),api('/api/health')]);render(status,health)}catch(err){document.getElementById('events').textContent='拉取状态失败: '+err.message}}document.getElementById('saveToken').onclick=async()=>{bearerToken=document.getElementById('token').value.trim();await refresh()};document.getElementById('btnEnable').onclick=async()=>{await api('/api/notify','POST',{enabled:true});await refresh()};document.getElementById('btnDisable').onclick=async()=>{await api('/api/notify','POST',{enabled:false});await refresh()};document.getElementById('btnTest').onclick=async()=>{await api('/api/test-notify','POST',{});await refresh()};document.getElementById('btnReload').onclick=async()=>{await api('/api/reload-config','POST',{});await refresh()};refresh();setInterval(refresh,5000);</script></body></html>"""


def _ensure_auth(runtime: MonitorRuntimeService, write: bool) -> Response | None:
    if runtime.is_authorized(request.headers.get("Authorization"), write=write):
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def create_app(runtime: MonitorRuntimeService) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        return _html_page()

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

