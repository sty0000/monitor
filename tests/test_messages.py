from monitor.state_machine import build_alert_message, build_recovered_message


def test_alert_message_contains_instance_name() -> None:
    sample = {
        "timestamp": "2026-05-14T00:00:00+00:00",
        "gpus": [{"index": 0, "utilization_gpu": 10, "memory_used_mb": 1000, "power_draw_w": 200, "temperature_c": 60, "compute_pids": [123]}],
    }
    subject, body = build_alert_message("server-a", "LOW_USAGE_ALERT", sample, "util too low")
    assert "[server-a]" in subject
    assert "monitor: server-a" in body


def test_recovered_message_contains_instance_name() -> None:
    sample = {
        "timestamp": "2026-05-14T00:00:00+00:00",
        "gpus": [{"index": 0, "utilization_gpu": 90, "memory_used_mb": 1000, "power_draw_w": 200, "temperature_c": 60, "compute_pids": [123]}],
    }
    subject, body = build_recovered_message("server-a", "LOW_USAGE_ALERT", sample)
    assert "[server-a]" in subject
    assert "monitor: server-a" in body
