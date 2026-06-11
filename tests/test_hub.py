from __future__ import annotations

from pathlib import Path

from monitor.hub import HubNode, collect_nodes, create_app, load_hub_auth_token, load_nodes


def test_load_nodes_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "nodes.yaml"
    path.write_text("nodes:\n  - name: a\n    base_url: http://127.0.0.1:8090\n    token: t\n", encoding="utf-8")

    nodes = load_nodes(path)

    assert nodes == [HubNode(name="a", base_url="http://127.0.0.1:8090", token="t", timeout_seconds=3.0)]


def test_hub_app_serves_summary(monkeypatch) -> None:
    def fake_collect(nodes):
        return {"ok": True, "nodes": [{"name": "a", "ok": True}]}

    monkeypatch.setattr("monitor.hub.collect_nodes", fake_collect)
    app = create_app([HubNode(name="a", base_url="http://x")])
    response = app.test_client().get("/api/summary")

    assert response.status_code == 200
    assert response.get_json()["nodes"][0]["name"] == "a"


def test_hub_auth_token_and_alias_api(tmp_path: Path) -> None:
    nodes_path = tmp_path / "nodes.yaml"
    nodes_path.write_text("hub:\n  auth_token: secret\nnodes: []\n", encoding="utf-8")
    assert load_hub_auth_token(nodes_path) == "secret"

    client = create_app([], auth_token="secret").test_client()
    assert client.get("/api/hub/summary").status_code == 401
    response = client.get("/api/hub/summary", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.get_json()["nodes"] == []


def test_hub_page_exposes_operational_columns() -> None:
    client = create_app([HubNode(name="a", base_url="http://x")]).test_client()

    html = client.get("/").get_data(as_text=True)

    assert "Last Sample" in html
    assert "Collector Errors" in html
    assert "Notifier" in html
    assert "@media(max-width:720px)" in html
