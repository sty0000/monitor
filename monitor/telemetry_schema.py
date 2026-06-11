from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TelemetryField:
    key: str
    metric_name: str
    description: str
    aliases: tuple[str, ...]


ADVANCED_TELEMETRY_FIELDS: tuple[TelemetryField, ...] = (
    TelemetryField("sm_clock_mhz", "gpu_monitor_gpu_sm_clock_mhz", "SM clock in MHz", ("smclk", "sm_clock", "sm_clock_mhz")),
    TelemetryField("mem_clock_mhz", "gpu_monitor_gpu_mem_clock_mhz", "Memory clock in MHz", ("memclk", "mem_clock", "mem_clock_mhz")),
    TelemetryField("pcie_tx_mb_s", "gpu_monitor_gpu_pcie_tx_mb_s", "PCIe transmit throughput in MB/s", ("pcietx", "pcie_tx", "pcie_tx_mb_s")),
    TelemetryField("pcie_rx_mb_s", "gpu_monitor_gpu_pcie_rx_mb_s", "PCIe receive throughput in MB/s", ("pcierx", "pcie_rx", "pcie_rx_mb_s")),
    TelemetryField("nvlink_tx_mb_s", "gpu_monitor_gpu_nvlink_tx_mb_s", "NVLink transmit throughput in MB/s", ("nvlink_tx", "nvlink_tx_mb_s")),
    TelemetryField("nvlink_rx_mb_s", "gpu_monitor_gpu_nvlink_rx_mb_s", "NVLink receive throughput in MB/s", ("nvlink_rx", "nvlink_rx_mb_s")),
    TelemetryField("ecc_error_count", "gpu_monitor_gpu_ecc_error_count", "ECC error count", ("ecc", "ecc_errors", "ecc_error_count")),
    TelemetryField("xid_error_count", "gpu_monitor_gpu_xid_error_count", "XID error count", ("xid", "xid_errors", "xid_error_count")),
)


def normalize_advanced_telemetry(raw: dict[str, Any] | None) -> dict[str, float]:
    if not raw:
        return {}
    normalized_input = {str(key).strip().lower().replace("-", "_"): value for key, value in raw.items()}
    normalized: dict[str, float] = {}
    for field in ADVANCED_TELEMETRY_FIELDS:
        for alias in field.aliases:
            value = normalized_input.get(alias.lower())
            if isinstance(value, (int, float)):
                normalized[field.key] = float(value)
                break
    return normalized
