from pathlib import Path

import monitor.agent as agent
import monitor.dashboard as dashboard
from monitor import service


def _write_config(path: Path) -> None:
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
notify:
  control:
    enabled: false
""",
        encoding="utf-8",
    )


def test_collect_once_uses_runtime_collector(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)

    class FakeCollector:
        def collect_sample(self, monitor, platform):
            return {"ok": True, "instance": monitor.instance_name, "sources": platform.telemetry_order}

    class FakeRuntime:
        def __init__(self, path):
            self.collector = FakeCollector()

    monkeypatch.setattr(service, "MonitorRuntimeService", FakeRuntime)

    sample = service.collect_once(config_path)

    assert sample["ok"] is True
    assert sample["sources"] == ["dcgm", "nvidia_smi"]


def test_run_agent_starts_and_stops_runtime(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    calls = []

    class StopEvent:
        def wait(self, seconds):
            calls.append(("wait", seconds))

        def is_set(self):
            return True

    class FakeRuntime:
        def __init__(self, path):
            self.stop_event = StopEvent()

        def start(self):
            calls.append(("start", None))

        def stop(self):
            calls.append(("stop", None))

    monkeypatch.setattr(service, "MonitorRuntimeService", FakeRuntime)

    assert service.run_agent(config_path) == 0
    assert calls == [("start", None), ("wait", 1), ("stop", None)]


def test_agent_main_delegates_to_service(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    calls = []
    monkeypatch.setattr(agent, "parse_args", lambda: type("Args", (), {"config": config_path, "once": False})())
    monkeypatch.setattr(agent, "run_agent", lambda path: calls.append(path) or 0)

    assert agent.main() == 0
    assert calls == [config_path]


def test_dashboard_main_delegates_to_service(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    calls = []
    monkeypatch.setattr(dashboard, "parse_args", lambda: type("Args", (), {"config": config_path, "host": "127.0.0.1", "port": 8099})())

    def fake_run_dashboard(path, host=None, port=None):
        calls.append((path, host, port))
        return 0

    monkeypatch.setattr("monitor.service.run_dashboard", fake_run_dashboard)

    assert dashboard.main() == 0
    assert calls == [(config_path, "127.0.0.1", 8099)]
