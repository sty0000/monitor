from __future__ import annotations

import os
import platform as platform_module
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MonitorConfig, PlatformConfig


class CollectorError(RuntimeError):
    def __init__(self, message: str, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class GPUStat:
    index: int
    uuid: str
    utilization_gpu: float | None
    memory_used_mb: float | None
    power_draw_w: float | None
    temperature_c: float | None
    device_error: str | None = None
    name: str = ""
    memory_total_mb: float | None = None
    dcgm: dict[str, Any] | None = None


def _is_error_text(raw: str) -> bool:
    text = raw.strip().upper()
    return "ERR" in text or "RESET" in text or "GPU REQUIRES" in text


def _to_float(raw: str) -> float | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_os_pretty_name(os_release: str) -> str:
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _to_int(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


class GPUCollector:
    def __init__(
        self,
        executable: str = "nvidia-smi",
        process_executable: str = "ps",
        dcgmi_executable: str = "dcgmi",
        meminfo_path: str | Path = "/proc/meminfo",
        os_release_path: str | Path = "/etc/os-release",
        dgx_dashboard_path: str | Path = "/opt/nvidia/dgx-dashboard-service",
    ) -> None:
        self.executable = executable
        self.process_executable = process_executable
        self.dcgmi_executable = dcgmi_executable
        self.meminfo_path = Path(meminfo_path)
        self.os_release_path = Path(os_release_path)
        self.dgx_dashboard_path = Path(dgx_dashboard_path)
        self.inventory_cache_ttl_seconds = 300
        self.driver_cache_ttl_seconds = 3600
        self._inventory_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._driver_version_cache: tuple[float, str] | None = None

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

    def _run_raw(self, cmd: list[str], timeout_seconds: int) -> str:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=timeout_seconds,
        )
        return completed.stdout

    def _run_process_list(self, timeout_seconds: int) -> str:
        cmd = [self.process_executable, "-eo", "pid,user,pcpu,pmem,rss,comm,args", "--no-headers"]
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=timeout_seconds,
        )
        return completed.stdout

    def query_process_usage(self, timeout_seconds: int, limit: int = 10) -> dict[str, Any]:
        try:
            output = self._run_process_list(timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "top_cpu": [], "top_memory": []}

        processes = []
        for line in output.splitlines():
            parts = line.split(None, 6)
            if len(parts) < 6:
                continue
            pid = _to_int(parts[0])
            cpu_percent = _to_float(parts[2])
            memory_percent = _to_float(parts[3])
            rss_kb = _to_int(parts[4])
            if pid is None:
                continue
            processes.append(
                {
                    "pid": pid,
                    "user": parts[1],
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory_percent,
                    "rss_mb": round((rss_kb or 0) / 1024, 1),
                    "command": parts[5],
                    "args": parts[6] if len(parts) >= 7 else parts[5],
                }
            )
        return {
            "ok": True,
            "error": "",
            "top_cpu": sorted(processes, key=lambda item: item.get("cpu_percent") or 0, reverse=True)[:limit],
            "top_memory": sorted(processes, key=lambda item: item.get("memory_percent") or 0, reverse=True)[:limit],
        }

    def _run_dcgm(self, args: list[str], timeout_seconds: int) -> str:
        return self._run_raw([self.dcgmi_executable, *args], timeout_seconds)

    def _read_text_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _query_gpu_inventory_uncached(self, timeout_seconds: int) -> list[dict[str, Any]]:
        try:
            output = self._run(["-L"], timeout_seconds)
        except CollectorError:
            return []
        inventory = []
        for line in output.splitlines():
            if not line.strip():
                continue
            index = None
            name = line.strip()
            if line.startswith("GPU ") and ":" in line:
                prefix, rest = line.split(":", 1)
                index = _to_int(prefix.replace("GPU", "").strip())
                name = rest.split("(", 1)[0].strip()
            inventory.append({"index": index, "name": name, "raw": line.strip()})
        return inventory

    def query_gpu_inventory(self, timeout_seconds: int, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force_refresh and self._inventory_cache is not None:
            cached_at, cached_value = self._inventory_cache
            if now - cached_at < self.inventory_cache_ttl_seconds:
                return list(cached_value)
        inventory = self._query_gpu_inventory_uncached(timeout_seconds)
        self._inventory_cache = (now, inventory)
        return list(inventory)

    def _query_driver_version_uncached(self, timeout_seconds: int) -> str:
        try:
            output = self._run(["--query-gpu=driver_version", "--format=csv,noheader"], timeout_seconds)
        except CollectorError:
            return ""
        versions = [line.strip() for line in output.splitlines() if line.strip()]
        return versions[0] if versions else ""

    def query_driver_version(self, timeout_seconds: int, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if not force_refresh and self._driver_version_cache is not None:
            cached_at, cached_value = self._driver_version_cache
            if now - cached_at < self.driver_cache_ttl_seconds:
                return cached_value
        version = self._query_driver_version_uncached(timeout_seconds)
        self._driver_version_cache = (now, version)
        return version

    def detect_platform(self, platform_config: PlatformConfig, timeout_seconds: int) -> dict[str, Any]:
        os_release = self._read_text_file(self.os_release_path)
        arch = platform_module.machine()
        inventory = self.query_gpu_inventory(timeout_seconds)
        inventory_text = " ".join(item.get("raw", "") for item in inventory).lower()
        os_text = os_release.lower()
        dashboard_exists = self.dgx_dashboard_path.exists()
        looks_like_dgx_spark = any(
            token in f"{inventory_text} {os_text}"
            for token in ["dgx spark", "gb10", "grace blackwell", "blackwell"]
        ) or dashboard_exists
        detected_profile = "dgx_spark" if looks_like_dgx_spark else "generic_nvidia"
        profile = detected_profile if platform_config.profile == "auto" else platform_config.profile
        nvml_available = False
        try:
            import pynvml  # type: ignore[import-not-found]  # noqa: F401
            nvml_available = True
        except Exception:  # noqa: BLE001
            nvml_available = False
        return {
            "profile": profile,
            "configured_profile": platform_config.profile,
            "detected_profile": detected_profile,
            "arch": arch,
            "os": _parse_os_pretty_name(os_release),
            "driver_version": self.query_driver_version(timeout_seconds),
            "gpu_inventory": inventory,
            "dgx_dashboard_present": dashboard_exists,
            "dcgm_available": shutil.which(self.dcgmi_executable) is not None,
            "nvml_available": nvml_available,
            "telemetry_source_active": "nvidia_smi",
            "telemetry_source_error": "",
        }

    def query_dcgm_health(self, timeout_seconds: int) -> dict[int, str]:
        output = self._run_dcgm(["health", "-c"], timeout_seconds)
        health: dict[int, str] = {}
        current_gpu: int | None = None
        for line in output.splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("gpu"):
                parts = stripped.replace(":", " ").split()
                for part in parts[1:]:
                    gpu_id = _to_int(part)
                    if gpu_id is not None:
                        current_gpu = gpu_id
                        break
            if current_gpu is not None and any(token in lower for token in ["fail", "error", "warning", "not ok", "unhealthy"]):
                health[current_gpu] = stripped
        return health

    def query_dcgm_extra_metrics(self, timeout_seconds: int) -> dict[int, dict[str, Any]]:
        output = self._run_dcgm(["dmon", "-c", "1"], timeout_seconds)
        metrics: dict[int, dict[str, Any]] = {}
        headers: list[str] = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                candidate = stripped.lstrip("#").strip().split()
                if candidate and any(token.lower() in {"gpu", "gpu_id", "gpuid"} for token in candidate):
                    headers = candidate
                continue
            parts = stripped.split()
            if not parts:
                continue
            gpu_index = _to_int(parts[0])
            if gpu_index is None:
                continue
            values = metrics.setdefault(gpu_index, {})
            for name, value in zip(headers[1:], parts[1:], strict=False):
                parsed = _to_float(value)
                values[name.lower()] = parsed if parsed is not None else value
        return metrics

    def query_nvml_stats(self, timeout_seconds: int) -> list[GPUStat]:
        try:
            import pynvml  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise CollectorError(f"pynvml import failed: {exc}", "nvml_unavailable") from exc
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            stats: list[GPUStat] = []
            for index in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                uuid = pynvml.nvmlDeviceGetUUID(handle)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(uuid, bytes):
                    uuid = uuid.decode("utf-8", errors="replace")
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                try:
                    power_draw_w = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                except Exception:  # noqa: BLE001
                    power_draw_w = None
                try:
                    temperature_c = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except Exception:  # noqa: BLE001
                    temperature_c = None
                stats.append(
                    GPUStat(
                        index=index,
                        uuid=str(uuid),
                        utilization_gpu=float(utilization.gpu),
                        memory_used_mb=round(float(memory.used) / 1024 / 1024, 1),
                        power_draw_w=power_draw_w,
                        temperature_c=float(temperature_c) if temperature_c is not None else None,
                        name=str(name),
                        memory_total_mb=round(float(memory.total) / 1024 / 1024, 1),
                    )
                )
            return stats
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, CollectorError):
                raise
            raise CollectorError(f"nvml failed: {exc}", "nvml_failed") from exc
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:  # noqa: BLE001
                pass

    def query_system_memory(self) -> dict[str, Any]:
        try:
            raw = self._read_text_file(self.meminfo_path)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        values: dict[str, int] = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            parts = rest.strip().split()
            if not parts:
                continue
            value = _to_int(parts[0])
            if value is not None:
                values[key] = value
        total_kb = values.get("MemTotal")
        available_kb = values.get("MemAvailable", values.get("MemFree"))
        if not total_kb or available_kb is None:
            return {"ok": False, "error": "MemTotal/MemAvailable unavailable"}
        used_kb = max(0, total_kb - available_kb)
        return {
            "ok": True,
            "total_mb": round(total_kb / 1024, 1),
            "available_mb": round(available_kb / 1024, 1),
            "used_mb": round(used_kb / 1024, 1),
            "used_percent": round((used_kb / total_kb) * 100, 1),
        }

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
            index = _to_int(parts[0])
            if index is None:
                continue
            field_names = ["utilization_gpu", "memory_used_mb", "power_draw_w", "temperature_c"]
            error_fields = [f"{name}={value}" for name, value in zip(field_names, parts[2:6], strict=False) if _is_error_text(value)]
            stats.append(
                GPUStat(
                    index=index,
                    uuid=parts[1],
                    utilization_gpu=_to_float(parts[2]),
                    memory_used_mb=_to_float(parts[3]),
                    power_draw_w=_to_float(parts[4]),
                    temperature_c=_to_float(parts[5]),
                    device_error="; ".join(error_fields) if error_fields else None,
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
            if gpu_index is None or pid is None or pid <= 0:
                continue
            mapping.setdefault(gpu_index, []).append(pid)
        return mapping

    def query_gpu_process_details(self, compute_map: dict[int, list[int]], process_usage: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
        by_pid: dict[int, dict[str, Any]] = {}
        for key in ["top_cpu", "top_memory"]:
            for proc in process_usage.get(key, []) or []:
                pid = proc.get("pid")
                if isinstance(pid, int):
                    by_pid[pid] = proc
        details: dict[int, list[dict[str, Any]]] = {}
        for gpu_index, pids in compute_map.items():
            rows = []
            for pid in pids:
                proc = by_pid.get(pid, {})
                rows.append({
                    "pid": pid,
                    "user": proc.get("user"),
                    "cpu_percent": proc.get("cpu_percent"),
                    "memory_percent": proc.get("memory_percent"),
                    "rss_mb": proc.get("rss_mb"),
                    "command": proc.get("command"),
                    "args": proc.get("args"),
                })
            details[gpu_index] = rows
        return details

    def collect_sample(self, monitor: MonitorConfig, platform: PlatformConfig | None = None) -> dict[str, object]:
        platform_config = platform or PlatformConfig()
        collector_errors: dict[str, str] = {}
        try:
            platform_summary = self.detect_platform(platform_config, monitor.command_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            collector_errors["platform"] = str(exc)
            platform_summary = {
                "profile": platform_config.profile,
                "configured_profile": platform_config.profile,
                "detected_profile": "unknown",
                "arch": platform_module.machine(),
                "os": "",
                "driver_version": "",
                "gpu_inventory": [],
                "dgx_dashboard_present": self.dgx_dashboard_path.exists(),
                "dcgm_available": shutil.which(self.dcgmi_executable) is not None,
                "telemetry_source_active": "nvidia_smi",
                "telemetry_source_error": str(exc),
            }
        dcgm_errors: dict[int, str] = {}
        dcgm_extra_metrics: dict[int, dict[str, Any]] = {}
        all_stats: list[GPUStat] | None = None
        for source in platform_config.telemetry_order:
            if source == "nvml":
                try:
                    all_stats = self.query_nvml_stats(monitor.command_timeout_seconds)
                    platform_summary["telemetry_source_active"] = "nvml"
                    platform_summary["telemetry_source_error"] = ""
                    collector_errors.pop("nvml", None)
                    break
                except Exception as exc:  # noqa: BLE001
                    platform_summary["telemetry_source_error"] = str(exc)
                    collector_errors["nvml"] = str(exc)
                    continue
            if source == "dcgm":
                if not platform_summary["dcgm_available"]:
                    platform_summary["telemetry_source_error"] = "dcgmi not found"
                    collector_errors["dcgm"] = "dcgmi not found"
                    continue
                try:
                    dcgm_errors = self.query_dcgm_health(monitor.command_timeout_seconds)
                    try:
                        dcgm_extra_metrics = self.query_dcgm_extra_metrics(monitor.command_timeout_seconds)
                    except Exception as exc:  # noqa: BLE001
                        collector_errors["dcgm_extra"] = str(exc)
                    platform_summary["telemetry_source_active"] = "dcgm"
                    platform_summary["telemetry_source_error"] = ""
                    collector_errors.pop("dcgm", None)
                    break
                except Exception as exc:  # noqa: BLE001
                    platform_summary["telemetry_source_error"] = str(exc)
                    collector_errors["dcgm"] = str(exc)
                    continue
            if source == "nvidia_smi":
                platform_summary["telemetry_source_active"] = "nvidia_smi"
                break

        if all_stats is None:
            try:
                all_stats = self.query_gpu_stats(monitor.command_timeout_seconds)
            except CollectorError as exc:
                collector_errors["nvidia_smi"] = str(exc)
                all_stats = []
        if dcgm_errors:
            patched_stats = []
            for gpu in all_stats:
                dcgm_error = dcgm_errors.get(gpu.index)
                if dcgm_error:
                    existing = gpu.device_error or ""
                    device_error = "; ".join(item for item in [existing, f"dcgm={dcgm_error}"] if item)
                    gpu = replace(gpu, device_error=device_error)
                if gpu.index in dcgm_extra_metrics:
                    gpu = replace(gpu, dcgm=dcgm_extra_metrics[gpu.index])
                patched_stats.append(gpu)
            all_stats = patched_stats
        elif dcgm_extra_metrics:
            all_stats = [replace(gpu, dcgm=dcgm_extra_metrics.get(gpu.index)) if gpu.index in dcgm_extra_metrics else gpu for gpu in all_stats]
        inventory_indexes = {item.get("index") for item in platform_summary.get("gpu_inventory", []) if item.get("index") is not None}
        stat_indexes = {gpu.index for gpu in all_stats}
        if stat_indexes and inventory_indexes and stat_indexes != inventory_indexes:
            try:
                platform_summary["gpu_inventory"] = self.query_gpu_inventory(monitor.command_timeout_seconds, force_refresh=True)
            except Exception as exc:  # noqa: BLE001
                collector_errors["inventory"] = str(exc)
        uuid_to_index = {gpu.uuid: gpu.index for gpu in all_stats}
        try:
            compute_map = self.query_compute_apps(uuid_to_index, monitor.command_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            collector_errors["compute_apps"] = str(exc)
            compute_map = {}
        selected = set(monitor.gpu_ids)
        stats = [gpu for gpu in all_stats if gpu.index in selected] if selected else all_stats
        process_usage = self.query_process_usage(monitor.command_timeout_seconds)
        gpu_process_details = self.query_gpu_process_details(compute_map, process_usage)
        if not process_usage.get("ok", False):
            collector_errors["process_usage"] = str(process_usage.get("error", "process usage unavailable"))
        system_memory = self.query_system_memory()
        if not system_memory.get("ok", False):
            collector_errors["system_memory"] = str(system_memory.get("error", "system memory unavailable"))
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gpu_count": len(stats),
            "gpu_ids": [gpu.index for gpu in stats],
            "gpus": [
                {
                    **asdict(gpu),
                    "compute_pids": compute_map.get(gpu.index, []),
                    "gpu_processes": gpu_process_details.get(gpu.index, []),
                }
                for gpu in stats
            ],
            "process_usage": process_usage,
            "system_memory": system_memory,
            "platform_summary": platform_summary,
            "collector_errors": collector_errors,
        }



