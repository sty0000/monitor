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

