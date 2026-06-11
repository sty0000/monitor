from __future__ import annotations

import copy
import difflib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import ConfigError, load_config
from .profile_templates import profile_to_updates


@dataclass(frozen=True)
class EditableField:
    path: str
    value_type: str
    description: str


EDITABLE_FIELDS: dict[str, EditableField] = {
    "scenario_profile": EditableField("scenario_profile", "enum", "Scenario profile"),
    "scenario_messages.low_usage_hint": EditableField("scenario_messages.low_usage_hint", "text", "Low usage scenario hint"),
    "scenario_messages.no_process_hint": EditableField("scenario_messages.no_process_hint", "text", "No process scenario hint"),
    "threshold.usage_percent": EditableField("threshold.usage_percent", "number", "GPU usage percent threshold"),
    "threshold.idle_minutes": EditableField("threshold.idle_minutes", "number", "Low-usage duration threshold"),
    "threshold.no_process_minutes": EditableField("threshold.no_process_minutes", "number", "No-process duration threshold"),
    "threshold.high_temperature_c": EditableField("threshold.high_temperature_c", "number", "High temperature threshold"),
    "threshold.low_usage_mode": EditableField("threshold.low_usage_mode", "enum", "Low-usage aggregation mode"),
    "threshold.primary_gpu_id": EditableField("threshold.primary_gpu_id", "integer|null", "Primary GPU id"),
    "alert.cooldown_minutes": EditableField("alert.cooldown_minutes", "number", "Alert cooldown minutes"),
    "alert.min_interval_minutes": EditableField("alert.min_interval_minutes", "number", "Global alert minimum interval"),
    "alert.runtime_error.enabled": EditableField("alert.runtime_error.enabled", "boolean", "Enable runtime error alerts"),
    "alert.runtime_error.consecutive_failures": EditableField("alert.runtime_error.consecutive_failures", "integer", "Runtime failure threshold"),
    "alert.recovery.enabled": EditableField("alert.recovery.enabled", "boolean", "Enable recovery notifications"),
    "alert.recovery.cooldown_minutes": EditableField("alert.recovery.cooldown_minutes", "number", "Recovery cooldown minutes"),
    "alert.recovery.severe_only": EditableField("alert.recovery.severe_only", "boolean", "Only notify severe recoveries"),
    "notify.control.enabled": EditableField("notify.control.enabled", "boolean", "Notification master switch"),
    "notify.control.low_usage_enabled": EditableField("notify.control.low_usage_enabled", "boolean", "Low usage notifications"),
    "notify.strategy.order": EditableField("notify.strategy.order", "list[string]", "Failover channel order"),
    "notify.strategy.fail_on_business_error": EditableField("notify.strategy.fail_on_business_error", "boolean", "Stop on business errors"),
    "notify.escalation.enabled": EditableField("notify.escalation.enabled", "boolean", "Enable alert escalation"),
    "history.enabled": EditableField("history.enabled", "boolean", "Enable history persistence"),
    "history.max_points_memory": EditableField("history.max_points_memory", "integer", "In-memory history limit"),
    "history.max_file_mb": EditableField("history.max_file_mb", "number", "History rotation size"),
    "history.retention_days": EditableField("history.retention_days", "integer", "History retention days"),
    "history.rotate_keep_files": EditableField("history.rotate_keep_files", "integer", "Rotated history files"),
    "history.query_default_limit": EditableField("history.query_default_limit", "integer", "Default history query limit"),
    "metrics.enabled": EditableField("metrics.enabled", "boolean", "Enable metrics endpoint"),
}


def editable_fields_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "fields": [field.__dict__ for field in EDITABLE_FIELDS.values()],
        "blocked_prefixes": ["dashboard", "logging", "monitor", "platform", "hub", "notify.smtp", "notify.webhook", "notify.telegram", "notify.feishu", "notify.wecom", "notify.dingtalk"],
    }


def read_config_data(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    return data


def _validate_editable_path(path: str) -> list[str]:
    if path not in EDITABLE_FIELDS:
        raise ConfigError(f"config field is not editable: {path}")
    return path.split(".")


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = _validate_editable_path(path)
    current = data
    for part in parts[:-1]:
        next_value = current.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ConfigError(f"config parent is not a mapping: {part}")
        current = next_value
    current[parts[-1]] = value


def _updates_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "profile" in payload:
        data = payload.get("config_data")
        if data is None:
            data = read_config_data(Path(str(payload.get("config_path", "config.yaml")))) if payload.get("config_path") else {}
        return profile_to_updates(str(payload["profile"]), data.get("profile_templates", {}) if isinstance(data, dict) else {})
    updates = payload.get("updates", payload)
    if not isinstance(updates, dict) or not updates:
        raise ConfigError("updates must be a non-empty object")
    return {str(path): value for path, value in updates.items()}


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def preview_config_update(config_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    updates = _updates_from_payload(payload)
    old_data = read_config_data(config_path)
    new_data = copy.deepcopy(old_data)
    changes = []
    for path, value in updates.items():
        old_value = _get_path(old_data, path)
        _set_path(new_data, path, value)
        changes.append({"path": path, "old_value": old_value, "new_value": value})
    old_yaml = _dump_yaml(old_data)
    new_yaml = _dump_yaml(new_data)
    diff = "".join(difflib.unified_diff(old_yaml.splitlines(True), new_yaml.splitlines(True), fromfile="config.yaml", tofile="config.yaml.preview"))
    tmp_path = config_path.with_name(f".{config_path.name}.preview-{os.getpid()}-{int(time.time() * 1000)}.tmp")
    try:
        tmp_path.write_text(new_yaml, encoding="utf-8")
        loaded = load_config(tmp_path)
        validation = {"ok": True, "warnings": loaded.warnings()}
    except Exception as exc:  # noqa: BLE001
        validation = {"ok": False, "error": str(exc)}
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return {"ok": bool(validation.get("ok")), "changes": changes, "validation": validation, "diff": diff}


def apply_config_update(config_path: Path, payload: dict[str, Any], reload_callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    preview = preview_config_update(config_path, payload)
    if not preview["validation"].get("ok"):
        return {"ok": False, "error": preview["validation"].get("error", "validation failed"), "preview": preview}
    old_bytes = config_path.read_bytes() if config_path.exists() else b""
    data = read_config_data(config_path)
    for change in preview["changes"]:
        _set_path(data, str(change["path"]), change["new_value"])
    backup_path = config_path.with_suffix(config_path.suffix + f".bak-{time.strftime('%Y%m%d%H%M%S')}")
    tmp_path = config_path.with_suffix(config_path.suffix + f".tmp-{os.getpid()}")
    backup_path.write_bytes(old_bytes)
    tmp_path.write_text(_dump_yaml(data), encoding="utf-8")
    os.replace(tmp_path, config_path)
    try:
        reload_result = reload_callback()
    except Exception as exc:  # noqa: BLE001
        config_path.write_bytes(old_bytes)
        try:
            reload_callback()
        except Exception:
            pass
        return {"ok": False, "backup_path": str(backup_path), "rolled_back": True, "error": str(exc), "preview": preview}
    return {"ok": True, "backup_path": str(backup_path), "rolled_back": False, "reload": reload_result, "preview": preview}


def preview_profile_update(config_path: Path, profile_name: str) -> dict[str, Any]:
    data = read_config_data(config_path)
    updates = profile_to_updates(profile_name, data.get("profile_templates", {}))
    return preview_config_update(config_path, {"updates": updates})


def apply_profile_update(config_path: Path, profile_name: str, reload_callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    data = read_config_data(config_path)
    updates = profile_to_updates(profile_name, data.get("profile_templates", {}))
    return apply_config_update(config_path, {"updates": updates}, reload_callback)
