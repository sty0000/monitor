from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .config import MonitorConfig


class CollectorError(RuntimeError):
    def __init__(self, message: str, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class GPUStat:
    index: int
    uuid: str
    utilization_gpu: float
    memory_used_mb: float
    power_draw_w: float
    temperature_c: float


def _to_float(raw: str) -> float:
    text = raw.strip()
    if text in {"", "N/A", "[Not Supported]"}:
        return -1.0
    return float(text)


def _to_int(raw: str) -> int:
    text = raw.strip()
    if text in {"", "N/A", "[Not Supported]"}:
        return -1
    return int(float(text))


class GPUCollector:
    def __init__(self, executable: str = "nvidia-smi") -> None:
        self.executable = executable

    def _run(self, args: list[str], timeout_seconds: int) -> str:
        cmd = [self.executable, *args]
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=timeout_seconds,
            )
            return completed.stdout
        except FileNotFoundError as exc:
            raise CollectorError("nvidia-smi not found in PATH", "command_not_found") from exc
        except subprocess.TimeoutExpired as exc:
            raise CollectorError(f"nvidia-smi timed out after {timeout_seconds}s", "command_timeout") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise CollectorError(f"nvidia-smi failed: {stderr}", "command_failed") from exc

    def query_gpu_stats(self, timeout_seconds: int) -> list[GPUStat]:
        output = self._run(
            [
                "--query-gpu=index,uuid,utilization.gpu,memory.used,power.draw,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout_seconds,
        )
        rows = [line.strip() for line in output.splitlines() if line.strip()]
        stats: list[GPUStat] = []
        for row in rows:
            parts = [part.strip() for part in row.split(",")]
            if len(parts) < 6:
                continue
            stats.append(
                GPUStat(
                    index=_to_int(parts[0]),
                    uuid=parts[1],
                    utilization_gpu=_to_float(parts[2]),
                    memory_used_mb=_to_float(parts[3]),
                    power_draw_w=_to_float(parts[4]),
                    temperature_c=_to_float(parts[5]),
                )
            )
        return stats

    def query_compute_apps(self, uuid_to_index: dict[str, int], timeout_seconds: int) -> dict[int, list[int]]:
        mapping: dict[int, list[int]] = {}
        try:
            output = self._run(
                ["--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"],
                timeout_seconds,
            )
        except CollectorError as exc:
            if "No running compute processes found" in str(exc):
                return mapping
            if exc.error_type == "command_failed" and "No running compute processes found" in str(exc):
                return mapping
            raise

        rows = [line.strip() for line in output.splitlines() if line.strip()]
        for row in rows:
            parts = [part.strip() for part in row.split(",")]
            if len(parts) < 2:
                continue
            gpu_uuid = parts[0]
            pid = _to_int(parts[1])
            gpu_index = uuid_to_index.get(gpu_uuid)
            if gpu_index is None or pid <= 0:
                continue
            mapping.setdefault(gpu_index, []).append(pid)
        return mapping

    def collect_sample(self, monitor: MonitorConfig) -> dict[str, object]:
        all_stats = self.query_gpu_stats(monitor.command_timeout_seconds)
        uuid_to_index = {gpu.uuid: gpu.index for gpu in all_stats}
        compute_map = self.query_compute_apps(uuid_to_index, monitor.command_timeout_seconds)
        selected = set(monitor.gpu_ids)
        stats = [gpu for gpu in all_stats if gpu.index in selected] if selected else all_stats
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gpu_count": len(stats),
            "gpu_ids": [gpu.index for gpu in stats],
            "gpus": [
                {
                    **asdict(gpu),
                    "compute_pids": compute_map.get(gpu.index, []),
                }
                for gpu in stats
            ],
        }

