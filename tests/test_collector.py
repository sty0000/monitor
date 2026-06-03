from pathlib import Path
from monitor.collector import GPUCollector
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
