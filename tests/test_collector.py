from pathlib import Path
import subprocess

import pytest

from monitor.collector import CollectorError, GPUCollector
from monitor.config import PlatformConfig


class StubCollector(GPUCollector):
    def __init__(self, outputs: list[str]) -> None:
        super().__init__("nvidia-smi")
        self.outputs = outputs

    def _run(self, args: list[str], timeout_seconds: int) -> str:
        return self.outputs.pop(0)

    def detect_platform(self, platform_config, timeout_seconds: int):
        return {
            "profile": "generic_nvidia",
            "configured_profile": "auto",
            "detected_profile": "generic_nvidia",
            "arch": "x86_64",
            "os": "",
            "driver_version": "",
            "gpu_inventory": [],
            "dgx_dashboard_present": False,
            "dcgm_available": False,
            "telemetry_source_active": "nvidia_smi",
            "telemetry_source_error": "",
        }


def test_collect_sample_reuses_single_gpu_snapshot() -> None:
    collector = StubCollector(
        [
            "0, GPU-0, 80, 1000, 200, 60\n1, GPU-1, 10, 2000, 180, 65\n",
            "GPU-0, 123\nGPU-1, 456\n",
        ]
    )
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})())
    assert sample["gpu_count"] == 2
    assert sample["gpus"][0]["compute_pids"] == [123]



def test_collect_sample_treats_bracketed_na_as_unavailable() -> None:
    collector = StubCollector(
        [
            "0, GPU-0, [N/A], 1000, [N/A], 60\n",
            "",
        ]
    )
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})())
    gpu = sample["gpus"][0]
    assert gpu["utilization_gpu"] is None
    assert gpu["power_draw_w"] is None


def test_collect_sample_treats_reset_required_as_unavailable() -> None:
    collector = StubCollector(
        [
            "0, GPU-0, [GPU requires reset], [GPU requires reset], [GPU requires reset], [GPU requires reset]\n",
            "",
        ]
    )
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})())
    gpu = sample["gpus"][0]
    assert gpu["utilization_gpu"] is None
    assert gpu["memory_used_mb"] is None
    assert gpu["power_draw_w"] is None
    assert gpu["temperature_c"] is None
    assert "GPU requires reset" in gpu["device_error"]


class ProcessStubCollector(GPUCollector):
    def _run_process_list(self, timeout_seconds: int) -> str:
        return (
            "123 root 90.5 10.0 204800 python python train.py --epochs 10\n"
            "456 user 10.0 60.5 1048576 java java -jar app.jar\n"
            "789 user 20.0 5.0 1024 bash bash\n"
        )


def test_query_process_usage_returns_cpu_and_memory_top_lists() -> None:
    collector = ProcessStubCollector()
    usage = collector.query_process_usage(timeout_seconds=8, limit=2)
    assert usage["ok"] is True
    assert [proc["pid"] for proc in usage["top_cpu"]] == [123, 789]
    assert [proc["pid"] for proc in usage["top_memory"]] == [456, 123]
    assert usage["top_memory"][0]["rss_mb"] == 1024.0


class DgxStubCollector(GPUCollector):
    def __init__(self, dcgm_error: Exception | None = None, meminfo_path=None) -> None:
        super().__init__(meminfo_path=meminfo_path or "/missing")
        self.dcgm_error = dcgm_error

    def query_gpu_inventory(self, timeout_seconds: int):
        return [{"index": 0, "name": "NVIDIA GB10", "raw": "GPU 0: NVIDIA GB10"}]

    def query_driver_version(self, timeout_seconds: int) -> str:
        return "999.1"

    def query_dcgm_health(self, timeout_seconds: int):
        if self.dcgm_error:
            raise self.dcgm_error
        return {0: "Health: Warning"}

    def query_gpu_stats(self, timeout_seconds: int):
        from monitor.collector import GPUStat

        return [GPUStat(index=0, uuid="GPU-0", utilization_gpu=10, memory_used_mb=None, power_draw_w=None, temperature_c=50)]

    def query_compute_apps(self, uuid_to_index, timeout_seconds: int):
        return {}

    def detect_platform(self, platform_config, timeout_seconds: int):
        return {
            "profile": "dgx_spark",
            "configured_profile": platform_config.profile,
            "detected_profile": "dgx_spark",
            "arch": "aarch64",
            "os": "DGX OS",
            "driver_version": "999.1",
            "gpu_inventory": [{"index": 0, "name": "NVIDIA GB10", "raw": "GPU 0: NVIDIA GB10"}],
            "dgx_dashboard_present": True,
            "dcgm_available": True,
            "telemetry_source_active": "nvidia_smi",
            "telemetry_source_error": "",
        }

    def query_process_usage(self, timeout_seconds: int, limit: int = 10):
        return {"ok": True, "error": "", "top_cpu": [], "top_memory": []}


def test_collect_sample_uses_dcgm_when_available() -> None:
    collector = DgxStubCollector()
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})(), PlatformConfig(profile="auto"))
    assert sample["platform_summary"]["profile"] == "dgx_spark"
    assert sample["platform_summary"]["telemetry_source_active"] == "dcgm"
    assert "dcgm=Health: Warning" in sample["gpus"][0]["device_error"]


def test_collect_sample_falls_back_when_dcgm_fails() -> None:
    collector = DgxStubCollector(dcgm_error=RuntimeError("dcgm failed"))
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})(), PlatformConfig(profile="auto"))
    assert sample["platform_summary"]["telemetry_source_active"] == "nvidia_smi"
    assert sample["platform_summary"]["telemetry_source_error"] == "dcgm failed"


def test_query_system_memory_reads_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       1024000 kB\nMemAvailable:    256000 kB\n", encoding="utf-8")
    collector = GPUCollector(meminfo_path=meminfo)
    memory = collector.query_system_memory()
    assert memory["ok"] is True
    assert memory["total_mb"] == 1000.0
    assert memory["available_mb"] == 250.0
    assert memory["used_percent"] == 75.0



class PartialFailureCollector(DgxStubCollector):
    def query_compute_apps(self, uuid_to_index, timeout_seconds: int):
        raise RuntimeError("compute apps failed")

    def query_process_usage(self, timeout_seconds: int, limit: int = 10):
        return {"ok": False, "error": "ps failed", "top_cpu": [], "top_memory": []}


def test_collect_sample_keeps_running_when_optional_domains_fail(tmp_path: Path) -> None:
    collector = PartialFailureCollector(meminfo_path=tmp_path / "missing-meminfo")
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})(), PlatformConfig(profile="auto"))

    assert sample["gpu_count"] == 1
    assert sample["gpus"][0]["compute_pids"] == []
    assert sample["process_usage"]["ok"] is False
    assert sample["system_memory"]["ok"] is False
    assert sample["collector_errors"]["compute_apps"] == "compute apps failed"
    assert sample["collector_errors"]["process_usage"] == "ps failed"
    assert "system_memory" in sample["collector_errors"]


def test_dcgm_fallback_is_recorded_as_local_collector_error() -> None:
    collector = DgxStubCollector(dcgm_error=RuntimeError("dcgm failed"))
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})(), PlatformConfig(profile="auto"))

    assert sample["platform_summary"]["telemetry_source_active"] == "nvidia_smi"
    assert sample["collector_errors"]["dcgm"] == "dcgm failed"



def test_collect_sample_joins_gpu_process_details() -> None:
    collector = StubCollector([
        "0, GPU-0, 80, 1000, 200, 60\n",
        "GPU-0, 123\n",
    ])
    collector.query_process_usage = lambda timeout_seconds, limit=10: {
        "ok": True,
        "top_cpu": [{"pid": 123, "user": "alice", "cpu_percent": 90, "memory_percent": 10, "rss_mb": 512, "command": "python", "args": "python train.py"}],
        "top_memory": [],
    }
    sample = collector.collect_sample(type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})())

    assert sample["gpus"][0]["gpu_processes"][0]["user"] == "alice"
    assert sample["gpus"][0]["gpu_processes"][0]["command"] == "python"


class CountingCollector(GPUCollector):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ...]] = []

    def _run(self, args: list[str], timeout_seconds: int) -> str:
        self.calls.append(tuple(args))
        if args == ["-L"]:
            return "GPU 0: NVIDIA Test (UUID: GPU-0)\n"
        if args == ["--query-gpu=driver_version", "--format=csv,noheader"]:
            return "580.1\n"
        if args == ["--query-gpu=index,uuid,utilization.gpu,memory.used,power.draw,temperature.gpu", "--format=csv,noheader,nounits"]:
            return "0, GPU-0, 80, 1000, 200, 60\n"
        if args == ["--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"]:
            return ""
        return ""

    def query_process_usage(self, timeout_seconds: int, limit: int = 10):
        return {"ok": True, "error": "", "top_cpu": [], "top_memory": []}


def test_nvidia_smi_timeout_becomes_collector_error(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    collector = GPUCollector()

    with pytest.raises(CollectorError) as exc_info:
        collector._run(["-L"], timeout_seconds=1)

    assert exc_info.value.error_type == "command_timeout"


def test_collect_sample_calls_main_gpu_query_once_and_caches_inventory_driver() -> None:
    collector = CountingCollector()
    cfg = type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})()

    first = collector.collect_sample(cfg, PlatformConfig(profile="auto"))
    second = collector.collect_sample(cfg, PlatformConfig(profile="auto"))

    main_query = ("--query-gpu=index,uuid,utilization.gpu,memory.used,power.draw,temperature.gpu", "--format=csv,noheader,nounits")
    assert first["gpu_count"] == 1
    assert second["gpu_count"] == 1
    assert collector.calls.count(main_query) == 2
    assert collector.calls.count(("-L",)) == 1
    assert collector.calls.count(("--query-gpu=driver_version", "--format=csv,noheader")) == 1


def test_inventory_cache_can_be_forced_on_topology_change() -> None:
    collector = CountingCollector()
    collector._inventory_cache = (0, [{"index": 9, "name": "old", "raw": "old"}])
    cfg = type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})()

    collector.collect_sample(cfg, PlatformConfig(profile="auto"))

    assert collector.calls.count(("-L",)) == 1


class ComputeAppsFailureCountingCollector(CountingCollector):
    def _run(self, args: list[str], timeout_seconds: int) -> str:
        if args == ["--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"]:
            raise CollectorError("compute query failed", "command_failed")
        return super()._run(args, timeout_seconds)


def test_compute_apps_failure_is_local_collector_error() -> None:
    collector = ComputeAppsFailureCountingCollector()
    cfg = type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})()

    sample = collector.collect_sample(cfg, PlatformConfig(profile="auto"))

    assert sample["gpu_count"] == 1
    assert sample["collector_errors"]["compute_apps"] == "compute query failed"


def test_config_accepts_nvml_in_telemetry_order(tmp_path: Path) -> None:
    from monitor.config import load_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
platform:
  telemetry_order: [nvml, dcgm, nvidia_smi]
dashboard:
  auth:
    enabled: false
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.platform.telemetry_order == ["nvml", "dcgm", "nvidia_smi"]

def test_query_nvml_stats_uses_optional_pynvml(monkeypatch) -> None:
    import sys
    import types

    fake = types.SimpleNamespace()
    fake.NVML_TEMPERATURE_GPU = 0
    fake.nvmlInit = lambda: None
    fake.nvmlShutdown = lambda: None
    fake.nvmlDeviceGetCount = lambda: 1
    fake.nvmlDeviceGetHandleByIndex = lambda index: {"index": index}
    fake.nvmlDeviceGetUUID = lambda handle: b"GPU-0"
    fake.nvmlDeviceGetName = lambda handle: b"NVIDIA Test"
    fake.nvmlDeviceGetUtilizationRates = lambda handle: types.SimpleNamespace(gpu=77)
    fake.nvmlDeviceGetMemoryInfo = lambda handle: types.SimpleNamespace(used=1024 * 1024 * 512, total=1024 * 1024 * 2048)
    fake.nvmlDeviceGetPowerUsage = lambda handle: 123000
    fake.nvmlDeviceGetTemperature = lambda handle, sensor: 66
    monkeypatch.setitem(sys.modules, "pynvml", fake)

    stats = GPUCollector().query_nvml_stats(timeout_seconds=1)

    assert stats[0].uuid == "GPU-0"
    assert stats[0].name == "NVIDIA Test"
    assert stats[0].utilization_gpu == 77
    assert stats[0].memory_total_mb == 2048.0
    assert stats[0].power_draw_w == 123.0

def test_collect_sample_uses_nvml_then_skips_nvidia_smi_main_query(monkeypatch) -> None:
    import sys
    import types

    fake = types.SimpleNamespace()
    fake.NVML_TEMPERATURE_GPU = 0
    fake.nvmlInit = lambda: None
    fake.nvmlShutdown = lambda: None
    fake.nvmlDeviceGetCount = lambda: 1
    fake.nvmlDeviceGetHandleByIndex = lambda index: {"index": index}
    fake.nvmlDeviceGetUUID = lambda handle: "GPU-0"
    fake.nvmlDeviceGetName = lambda handle: "NVIDIA Test"
    fake.nvmlDeviceGetUtilizationRates = lambda handle: types.SimpleNamespace(gpu=80)
    fake.nvmlDeviceGetMemoryInfo = lambda handle: types.SimpleNamespace(used=1024 * 1024, total=1024 * 1024 * 8)
    fake.nvmlDeviceGetPowerUsage = lambda handle: 100000
    fake.nvmlDeviceGetTemperature = lambda handle, sensor: 55
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    collector = CountingCollector()
    cfg = type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})()

    sample = collector.collect_sample(cfg, PlatformConfig(profile="auto", telemetry_order=["nvml", "nvidia_smi"]))

    main_query = ("--query-gpu=index,uuid,utilization.gpu,memory.used,power.draw,temperature.gpu", "--format=csv,noheader,nounits")
    assert sample["platform_summary"]["telemetry_source_active"] == "nvml"
    assert sample["gpus"][0]["memory_total_mb"] == 8.0
    assert collector.calls.count(main_query) == 0


def test_collect_sample_falls_back_when_nvml_unavailable(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "pynvml", None)
    collector = CountingCollector()
    cfg = type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})()

    sample = collector.collect_sample(cfg, PlatformConfig(profile="auto", telemetry_order=["nvml", "nvidia_smi"]))

    assert sample["platform_summary"]["telemetry_source_active"] == "nvidia_smi"
    assert "nvml" in sample["collector_errors"]
    assert sample["gpu_count"] == 1

class DcgmExtraCollector(CountingCollector):
    def detect_platform(self, platform_config: PlatformConfig, timeout_seconds: int):
        summary = super().detect_platform(platform_config, timeout_seconds)
        summary["dcgm_available"] = True
        return summary

    def _run_dcgm(self, args: list[str], timeout_seconds: int) -> str:
        if args == ["health", "-c"]:
            return "GPU 0: OK\n"
        if args == ["dmon", "-c", "1"]:
            return "# gpu smclk mclk pcie\n0 1200 5000 2\n"
        return ""


def test_dcgm_extra_metrics_are_attached_to_gpu() -> None:
    collector = DcgmExtraCollector()
    cfg = type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})()

    sample = collector.collect_sample(cfg, PlatformConfig(profile="auto", telemetry_order=["dcgm", "nvidia_smi"]))

    assert sample["gpus"][0]["dcgm"] == {"smclk": 1200.0, "mclk": 5000.0, "pcie": 2.0}


def test_dcgm_extra_failure_is_local_collector_error() -> None:
    class BrokenDcgmExtraCollector(DcgmExtraCollector):
        def query_dcgm_extra_metrics(self, timeout_seconds: int):
            raise RuntimeError("dmon failed")

    collector = BrokenDcgmExtraCollector()
    cfg = type("cfg", (), {"command_timeout_seconds": 8, "gpu_ids": []})()

    sample = collector.collect_sample(cfg, PlatformConfig(profile="auto", telemetry_order=["dcgm", "nvidia_smi"]))

    assert sample["collector_errors"]["dcgm_extra"] == "dmon failed"