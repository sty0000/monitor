from monitor.collector import GPUCollector


class StubCollector(GPUCollector):
    def __init__(self, outputs: list[str]) -> None:
        super().__init__("nvidia-smi")
        self.outputs = outputs

    def _run(self, args: list[str], timeout_seconds: int) -> str:
        return self.outputs.pop(0)


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
    assert gpu["utilization_gpu"] == -1.0
    assert gpu["power_draw_w"] == -1.0
