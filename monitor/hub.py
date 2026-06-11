from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, jsonify, request


@dataclass(frozen=True)
class HubNode:
    name: str
    base_url: str
    token: str = ""
    timeout_seconds: float = 3.0


def load_hub_auth_token(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    hub = raw.get("hub", {}) if isinstance(raw, dict) else {}
    return str(hub.get("auth_token") or "")


def load_nodes(path: Path) -> list[HubNode]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    nodes = raw.get("nodes", raw if isinstance(raw, list) else [])
    result: list[HubNode] = []
    for item in nodes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("base_url") or "node")
        base_url = str(item.get("base_url") or item.get("url") or "").rstrip("/")
        if not base_url:
            continue
        result.append(
            HubNode(
                name=name,
                base_url=base_url,
                token=str(item.get("token") or ""),
                timeout_seconds=float(item.get("timeout_seconds") or 3),
            )
        )
    return result


def fetch_json(node: HubNode, path: str) -> tuple[bool, Any]:
    request = urllib.request.Request(node.base_url + path)
    request.add_header("Accept", "application/json")
    if node.token:
        request.add_header("Authorization", f"Bearer {node.token}")
    try:
        with urllib.request.urlopen(request, timeout=node.timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            return True, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, {"error": f"HTTP {exc.code}: {body[:200]}"}
    except Exception as exc:  # noqa: BLE001
        return False, {"error": str(exc)}


def collect_nodes(nodes: list[HubNode]) -> dict[str, Any]:
    rows = []
    for node in nodes:
        health_ok, health = fetch_json(node, "/api/health")
        status_ok, status = fetch_json(node, "/api/status") if health_ok else (False, {})
        sample = (status or {}).get("sample", {}) if isinstance(status, dict) else {}
        rows.append(
            {
                "name": node.name,
                "base_url": node.base_url,
                "ok": bool(health_ok and status_ok),
                "health": health,
                "monitor_state": (health or {}).get("monitor_state") if isinstance(health, dict) else None,
                "gpu_count": sample.get("gpu_count"),
                "gpu_ids": sample.get("gpu_ids", []),
                "version": (status or {}).get("version") if isinstance(status, dict) else None,
                "last_sample_timestamp": (health or {}).get("last_sample_timestamp") if isinstance(health, dict) else None,
                "active_alert": (status or {}).get("active_alert") if isinstance(status, dict) else None,
                "collector_errors": sample.get("collector_errors", {}),
                "notifier_health": (status or {}).get("notifier_health", {}) if isinstance(status, dict) else {},
                "platform_summary": (status or {}).get("platform_summary", {}) if isinstance(status, dict) else {},
                "error": None if health_ok and status_ok else (health or status or {}).get("error", "unreachable"),
            }
        )
    return {"ok": all(row["ok"] for row in rows), "nodes": rows}


def create_app(nodes: list[HubNode], auth_token: str = "") -> Flask:
    app = Flask(__name__)

    def _authorized() -> bool:
        if not auth_token:
            return True
        header = request.headers.get("Authorization", "")
        return header == f"Bearer {auth_token}"

    @app.get("/")
    def index() -> str:
        return """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Monitor Hub</title>
<style>body{font-family:sans-serif;margin:24px;background:#0f172a;color:#e2e8f0}table{border-collapse:collapse;width:100%}td,th{border:1px solid #334155;padding:8px;vertical-align:top}.ok{color:#22c55e}.bad{color:#ef4444}.muted{color:#94a3b8}@media(max-width:720px){body{margin:12px}table{font-size:12px;display:block;overflow-x:auto;white-space:nowrap}}</style>
</head><body><h1>Monitor Hub</h1><p class="muted">Lightweight read-only aggregation. It does not execute remote commands.</p><table><thead><tr><th>Node</th><th>URL</th><th>OK</th><th>Version</th><th>State</th><th>Last Sample</th><th>GPUs</th><th>Alert</th><th>Collector Errors</th><th>Notifier</th><th>Error</th></tr></thead><tbody id="body"></tbody></table>
<script>
function keys(o){return Object.keys(o||{}).join(',')}
function refresh(){fetch('/api/summary').then(r=>r.json()).then(data=>{document.getElementById('body').innerHTML=data.nodes.map(n=>'<tr><td>'+n.name+'</td><td>'+n.base_url+'</td><td class="'+(n.ok?'ok':'bad')+'">'+n.ok+'</td><td>'+(n.version||'')+'</td><td>'+n.monitor_state+'</td><td>'+(n.last_sample_timestamp||'')+'</td><td>'+((n.gpu_ids||[]).join(','))+'</td><td>'+((n.active_alert)||'')+'</td><td>'+keys(n.collector_errors)+'</td><td>'+keys(n.notifier_health)+'</td><td>'+(n.error||'')+'</td></tr>').join('')})}
refresh();setInterval(refresh,5000)
</script></body></html>
"""

    @app.get("/api/summary")
    @app.get("/api/hub/summary")
    def summary() -> Any:
        if not _authorized():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return jsonify(collect_nodes(nodes))

    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a lightweight multi-instance monitor hub")
    parser.add_argument("--nodes", type=Path, default=Path("hub.nodes.yaml"), help="YAML file with monitor nodes")
    parser.add_argument("--host", default="127.0.0.1", help="Hub listen host")
    parser.add_argument("--port", type=int, default=8099, help="Hub listen port")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    nodes = load_nodes(args.nodes)
    app = create_app(nodes, auth_token=load_hub_auth_token(args.nodes))
    app.run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
