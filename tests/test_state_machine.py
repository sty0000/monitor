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

