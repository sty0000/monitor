from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import ConfigError

BUILTIN_PROFILE_NAMES = {"custom", "training", "inference"}
PROFILE_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
ALLOWED_THRESHOLD_FIELDS = {"usage_percent", "idle_minutes", "no_process_minutes", "low_usage_mode"}
ALLOWED_MESSAGE_FIELDS = {"low_usage_hint", "no_process_hint"}
LOW_USAGE_MODES = {"any", "all", "majority", "selected_primary"}


@dataclass(frozen=True)
class ProfileTemplate:
    name: str
    description: str = ""
    threshold: dict[str, Any] = field(default_factory=dict)
    scenario_messages: dict[str, str] = field(default_factory=dict)


def builtin_profile_templates() -> dict[str, ProfileTemplate]:
    return {
        "custom": ProfileTemplate(
            name="custom",
            description="Custom profile; keep explicit threshold values and generic messages.",
            scenario_messages={
                "low_usage_hint": "hint: utilization stayed below threshold; inspect workload, traffic and process state.",
                "no_process_hint": "hint: no compute process is visible; inspect workload process state.",
            },
        ),
        "training": ProfileTemplate(
            name="training",
            description="Training workload profile.",
            threshold={"usage_percent": 20, "idle_minutes": 10, "no_process_minutes": 5, "low_usage_mode": "any"},
            scenario_messages={
                "low_usage_hint": "training hint: utilization stayed low; check whether training is stuck, data loading is blocked, or the job is waiting unexpectedly.",
                "no_process_hint": "training hint: no compute process is visible; check whether the training process exited, crashed, or failed to start.",
            },
        ),
        "inference": ProfileTemplate(
            name="inference",
            description="LLM or model serving profile.",
            threshold={"usage_percent": 10, "idle_minutes": 30, "no_process_minutes": 10, "low_usage_mode": "all"},
            scenario_messages={
                "low_usage_hint": "inference hint: low utilization may be normal when traffic is low or the service is idle; check request rate before treating this as a failure.",
                "no_process_hint": "inference hint: no compute process is visible; check whether the serving process is still running, even if low traffic is expected.",
            },
        ),
    }


def _validate_profile_name(name: str) -> None:
    if not PROFILE_NAME_RE.match(name):
        raise ConfigError(f"profile_templates contains invalid profile name: {name}")


def parse_profile_template(name: str, payload: Any) -> ProfileTemplate:
    _validate_profile_name(name)
    if not isinstance(payload, dict):
        raise ConfigError(f"profile_templates.{name} must be a mapping")
    unknown_top = set(payload) - {"description", "threshold", "scenario_messages"}
    if unknown_top:
        raise ConfigError(f"profile_templates.{name} contains unknown field(s): " + ", ".join(sorted(unknown_top)))
    threshold = payload.get("threshold", {}) or {}
    if not isinstance(threshold, dict):
        raise ConfigError(f"profile_templates.{name}.threshold must be a mapping")
    unknown_threshold = set(threshold) - ALLOWED_THRESHOLD_FIELDS
    if unknown_threshold:
        raise ConfigError(f"profile_templates.{name}.threshold contains unknown field(s): " + ", ".join(sorted(unknown_threshold)))
    if "low_usage_mode" in threshold and str(threshold["low_usage_mode"]) not in LOW_USAGE_MODES:
        raise ConfigError(f"profile_templates.{name}.threshold.low_usage_mode must be one of any/all/majority/selected_primary")
    messages = payload.get("scenario_messages", {}) or {}
    if not isinstance(messages, dict):
        raise ConfigError(f"profile_templates.{name}.scenario_messages must be a mapping")
    unknown_messages = set(messages) - ALLOWED_MESSAGE_FIELDS
    if unknown_messages:
        raise ConfigError(f"profile_templates.{name}.scenario_messages contains unknown field(s): " + ", ".join(sorted(unknown_messages)))
    return ProfileTemplate(
        name=name,
        description=str(payload.get("description", "")),
        threshold=dict(threshold),
        scenario_messages={key: str(value) for key, value in messages.items()},
    )


def parse_profile_templates(raw_templates: Any) -> dict[str, ProfileTemplate]:
    if raw_templates is None:
        return {}
    if not isinstance(raw_templates, dict):
        raise ConfigError("profile_templates must be a mapping")
    return {str(name): parse_profile_template(str(name), payload) for name, payload in raw_templates.items()}


def available_profile_templates(raw_templates: Any = None) -> dict[str, ProfileTemplate]:
    templates = builtin_profile_templates()
    templates.update(parse_profile_templates(raw_templates))
    return templates


def profile_to_updates(profile_name: str, raw_templates: Any = None) -> dict[str, Any]:
    templates = available_profile_templates(raw_templates)
    if profile_name not in templates:
        raise ConfigError(f"unknown scenario_profile: {profile_name}; use one of " + ", ".join(sorted(templates)))
    template = templates[profile_name]
    updates: dict[str, Any] = {"scenario_profile": profile_name}
    for key, value in template.threshold.items():
        updates[f"threshold.{key}"] = value
    for key, value in template.scenario_messages.items():
        updates[f"scenario_messages.{key}"] = value
    return updates
