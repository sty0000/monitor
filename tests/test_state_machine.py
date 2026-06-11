import time

from monitor.config import AppConfig, DashboardAuthConfig, DashboardConfig
from monitor.state_machine import (
    MonitorState,
    can_send_recovery,
    evaluate_state,
    mark_alert_sent,
    should_send_by_global_interval,
    should_send_with_cooldown,
    build_alert_message,
)


def _config() -> AppConfig:
    return AppConfig(dashboard=DashboardConfig(auth=DashboardAuthConfig(enabled=False, token="")))


def test_state_machine_warmup_and_waiting() -> None:
    config = _config()
    state = MonitorState(start_ts=time.time())
    sample = {"gpus": []}
    assert evaluate_state(config, state, sample).state_name == "WARMUP"
    state.start_ts -= 600
    assert evaluate_state(config, state, sample).state_name == "WAITING_ACTIVE"


def test_state_machine_arming_transition() -> None:
    config = _config()
    state = MonitorState(start_ts=time.time() - 600)
    sample = {"gpus": [{"index": 0, "utilization_gpu": 90, "compute_pids": [123]}]}
    assert evaluate_state(config, state, sample).state_name == "ARMING"
    result = evaluate_state(config, state, sample, now=time.time() + 120)
    assert result.state_name in {"ACTIVE", "ARMING"}


def test_alert_cooldown_marks_only_after_send() -> None:
    state = MonitorState(start_ts=time.time())
    now = time.time()
    assert should_send_with_cooldown(state, "LOW_USAGE_ALERT", 60, now)
    assert should_send_by_global_interval(state, 60, now)
    assert "LOW_USAGE_ALERT" not in state.last_alert_sent_at
    assert state.last_any_alert_sent_at is None
    mark_alert_sent(state, "LOW_USAGE_ALERT", now)
    assert not should_send_with_cooldown(state, "LOW_USAGE_ALERT", 60, now + 10)
    assert not should_send_by_global_interval(state, 60, now + 10)


def test_recovery_respects_config() -> None:
    config = _config()
    assert can_send_recovery(config, "LOW_USAGE_ALERT")



def test_state_machine_high_temperature_alert_after_duration() -> None:
    config = _config()
    state = MonitorState(start_ts=time.time() - 600)
    now = time.time()
    sample = {"gpus": [{"index": 0, "temperature_c": 86, "utilization_gpu": 0, "compute_pids": []}]}

    first = evaluate_state(config, state, sample, now=now)
    assert first.state_name == "WAITING_ACTIVE"

    second = evaluate_state(config, state, sample, now=now + (config.threshold.high_temperature_minutes * 60) + 1)
    assert second.state_name == "HIGH_TEMPERATURE_ALERT"
    assert second.alert_key == "HIGH_TEMPERATURE_ALERT"


def test_state_machine_high_temperature_recovers_when_back_to_normal() -> None:
    config = _config()
    state = MonitorState(start_ts=time.time() - 600)
    now = time.time()
    hot_sample = {"gpus": [{"index": 0, "temperature_c": 90, "utilization_gpu": 0, "compute_pids": []}]}
    cool_sample = {"gpus": [{"index": 0, "temperature_c": 70, "utilization_gpu": 0, "compute_pids": []}]}

    evaluate_state(config, state, hot_sample, now=now)
    hot_result = evaluate_state(config, state, hot_sample, now=now + (config.threshold.high_temperature_minutes * 60) + 1)
    assert hot_result.state_name == "HIGH_TEMPERATURE_ALERT"

    recovered = evaluate_state(config, state, cool_sample, now=now + (config.threshold.high_temperature_minutes * 60) + 2)
    assert recovered.state_name == "WAITING_ACTIVE"



def test_state_machine_gpu_error_alert_can_be_muted() -> None:
    config = _config()
    state = MonitorState(start_ts=time.time() - 600)
    sample = {"gpus": [{"index": 2, "device_error": "temperature_c=[GPU requires reset]", "compute_pids": []}]}

    result = evaluate_state(config, state, sample, now=time.time())
    assert result.state_name == "GPU_ERROR_ALERT"
    assert result.alert_key == "GPU_ERROR_ALERT"

    config.alert.gpu_error.muted_gpu_ids.append(2)
    muted_result = evaluate_state(config, state, sample, now=time.time())
    assert muted_result.state_name == "WAITING_ACTIVE"



def _sample(utils: list[int]) -> dict[str, object]:
    return {"gpus": [{"index": idx, "utilization_gpu": util, "compute_pids": [idx + 100]} for idx, util in enumerate(utils)]}


def test_low_usage_mode_all_requires_all_gpus_below_threshold() -> None:
    config = _config()
    object.__setattr__(config.threshold, "low_usage_mode", "all")
    state = MonitorState(start_ts=time.time() - 600, armed=True)

    assert evaluate_state(config, state, _sample([1, 90]), now=time.time()).state_name == "ACTIVE"
    state = MonitorState(start_ts=time.time() - 600, armed=True)
    first = evaluate_state(config, state, _sample([1, 2]), now=time.time())
    result = evaluate_state(config, state, _sample([1, 2]), now=time.time() + config.threshold.idle_minutes * 60 + 1)
    assert first.state_name == "ACTIVE"
    assert result.state_name == "LOW_USAGE_ALERT"


def test_low_usage_mode_majority_requires_more_than_half_below_threshold() -> None:
    config = _config()
    object.__setattr__(config.threshold, "low_usage_mode", "majority")
    state = MonitorState(start_ts=time.time() - 600, armed=True)

    first = evaluate_state(config, state, _sample([1, 2, 90]), now=time.time())
    result = evaluate_state(config, state, _sample([1, 2, 90]), now=time.time() + config.threshold.idle_minutes * 60 + 1)
    assert first.state_name == "ACTIVE"
    assert result.state_name == "LOW_USAGE_ALERT"
    state = MonitorState(start_ts=time.time() - 600)
    assert evaluate_state(config, state, _sample([1, 90, 90]), now=time.time()).state_name == "ARMING"


def test_low_usage_mode_selected_primary_uses_configured_gpu_only() -> None:
    config = _config()
    object.__setattr__(config.threshold, "low_usage_mode", "selected_primary")
    object.__setattr__(config.threshold, "primary_gpu_id", 1)
    state = MonitorState(start_ts=time.time() - 600)

    assert evaluate_state(config, state, _sample([1, 90]), now=time.time()).state_name == "ARMING"
    state = MonitorState(start_ts=time.time() - 600, armed=True)
    first = evaluate_state(config, state, _sample([90, 1]), now=time.time())
    result = evaluate_state(config, state, _sample([90, 1]), now=time.time() + config.threshold.idle_minutes * 60 + 1)
    assert first.state_name == "ACTIVE"
    assert result.state_name == "LOW_USAGE_ALERT"


def test_low_usage_mode_selected_primary_missing_gpu_does_not_alert() -> None:
    config = _config()
    object.__setattr__(config.threshold, "low_usage_mode", "selected_primary")
    object.__setattr__(config.threshold, "primary_gpu_id", 99)
    state = MonitorState(start_ts=time.time() - 600, armed=True)

    assert evaluate_state(config, state, _sample([1, 1]), now=time.time()).state_name == "ACTIVE"


def test_recovery_severe_only_filters_low_usage() -> None:
    config = _config()
    object.__setattr__(config.alert.recovery, "severe_only", True)

    assert not can_send_recovery(config, "LOW_USAGE_ALERT")
    assert can_send_recovery(config, "GPU_ERROR_ALERT")


def test_alert_message_uses_training_hint_for_low_usage() -> None:
    _, body = build_alert_message(
        "train-node",
        "LOW_USAGE_ALERT",
        {"timestamp": "2026-01-01T00:00:00Z", "gpus": [{"index": 0, "utilization_gpu": 1, "memory_used_mb": 1000, "power_draw_w": 50, "temperature_c": 60, "compute_pids": [123]}]},
        "low util",
        "training",
    )

    assert "scenario_profile: training" in body
    assert "training is stuck" in body
    assert "data loading is blocked" in body


def test_alert_message_uses_inference_hint_without_training_failure_language() -> None:
    _, body = build_alert_message(
        "serve-node",
        "LOW_USAGE_ALERT",
        {"timestamp": "2026-01-01T00:00:00Z", "gpus": [{"index": 0, "utilization_gpu": 1, "memory_used_mb": 40000, "power_draw_w": 60, "temperature_c": 58, "compute_pids": [456]}]},
        "low util",
        "inference",
    )

    assert "scenario_profile: inference" in body
    assert "traffic is low" in body
    assert "service is idle" in body
    assert "training is stuck" not in body


def test_alert_message_custom_uses_generic_hint() -> None:
    _, body = build_alert_message(
        "gpu-node",
        "LOW_USAGE_ALERT",
        {"timestamp": "2026-01-01T00:00:00Z", "gpus": [{"index": 0, "utilization_gpu": 1, "memory_used_mb": 1000, "power_draw_w": 50, "temperature_c": 60, "compute_pids": [123]}]},
        "low util",
        "custom",
    )

    assert "scenario_profile: custom" in body
    assert "inspect workload, traffic and process state" in body
    assert "training is stuck" not in body
    assert "service is idle" not in body


def test_no_process_message_uses_training_process_hint() -> None:
    _, body = build_alert_message(
        "train-node",
        "NO_PROCESS_ALERT",
        {"timestamp": "2026-01-01T00:00:00Z", "gpus": [{"index": 0, "utilization_gpu": 0, "memory_used_mb": 0, "power_draw_w": 30, "temperature_c": 40, "compute_pids": []}]},
        "no process",
        "training",
    )

    assert "training process exited" in body


def test_alert_message_prefers_scenario_messages_over_profile_hint() -> None:
    _, body = build_alert_message(
        "node",
        "LOW_USAGE_ALERT",
        {"timestamp": "2026-01-01T00:00:00Z", "gpus": [{"index": 0, "utilization_gpu": 1, "memory_used_mb": 1000, "power_draw_w": 50, "temperature_c": 60, "compute_pids": [123]}]},
        "low util",
        "training",
        {"low_usage_hint": "custom low usage message"},
    )

    assert "custom low usage message" in body
    assert "training is stuck" not in body
