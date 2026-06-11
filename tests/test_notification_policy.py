from monitor.config import load_config
from monitor.notification_policy import apply_message_template, find_escalation, recovery_alert_key, safe_format
from monitor.state_machine import MonitorState, mark_alert_sent


def test_safe_format_keeps_missing_variables() -> None:
    assert safe_format("hello {name} {missing}", {"name": "gpu"}) == "hello gpu {missing}"


def test_templates_render_subject_and_body(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
notify:
  templates:
    HIGH_TEMPERATURE_ALERT:
      subject: "[{instance}] {alert}"
      body: "reason={reason} missing={missing}"
      body_format: markdown
""",
        encoding="utf-8",
    )
    config = load_config(path)

    subject, body = apply_message_template(
        config,
        "HIGH_TEMPERATURE_ALERT",
        "default subject",
        "default body",
        {"instance": "node-a", "alert": "HIGH_TEMPERATURE_ALERT", "reason": "hot"},
    )

    assert subject == "[node-a] HIGH_TEMPERATURE_ALERT"
    assert body == "reason=hot missing={missing}"


def test_escalation_rule_respects_after_minutes_and_cooldown(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
dashboard:
  auth:
    enabled: false
notify:
  escalation:
    enabled: true
    rules:
      - alert: HIGH_TEMPERATURE_ALERT
        after_minutes: 10
        channel: telegram
        cooldown_minutes: 30
""",
        encoding="utf-8",
    )
    config = load_config(path)
    state = MonitorState(start_ts=0)
    state.alert_first_seen_at["HIGH_TEMPERATURE_ALERT"] = 100

    assert find_escalation(config, state, "HIGH_TEMPERATURE_ALERT", 699) is None
    decision = find_escalation(config, state, "HIGH_TEMPERATURE_ALERT", 700)
    assert decision is not None
    assert decision.rule.channel == "telegram"
    mark_alert_sent(state, decision.key, 700)
    assert find_escalation(config, state, "HIGH_TEMPERATURE_ALERT", 701) is None


def test_recovery_alert_key_is_specific() -> None:
    assert recovery_alert_key("HIGH_TEMPERATURE_ALERT") == "HIGH_TEMPERATURE_RECOVERED"
    assert recovery_alert_key("GPU_ERROR_ALERT") == "GPU_ERROR_RECOVERED"
    assert recovery_alert_key("RUNTIME_ERROR_ALERT") == "RUNTIME_ERROR_RECOVERED"
    assert recovery_alert_key("LOW_USAGE_ALERT") == "LOW_USAGE_RECOVERED"
