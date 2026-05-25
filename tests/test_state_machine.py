import time

from monitor.config import AppConfig, DashboardAuthConfig, DashboardConfig
from monitor.state_machine import (
    MonitorState,
    can_send_recovery,
    evaluate_state,
    mark_alert_sent,
    should_send_by_global_interval,
    should_send_with_cooldown,
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

