from pathlib import Path

from monitor.config_edit import apply_config_update, apply_profile_update, editable_fields_payload, preview_config_update, preview_profile_update


def _write_config(path: Path) -> None:
    path.write_text(
        """
dashboard:
  auth:
    enabled: true
    token: secret-token
threshold:
  usage_percent: 20
""",
        encoding="utf-8",
    )


def test_editable_fields_exclude_secrets() -> None:
    payload = editable_fields_payload()
    paths = {field["path"] for field in payload["fields"]}

    assert "threshold.usage_percent" in paths
    assert "dashboard.auth.token" not in paths
    assert "notify.webhook.url" not in paths


def test_preview_rejects_invalid_config_value(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)

    result = preview_config_update(config_path, {"updates": {"threshold.low_usage_mode": "bad"}})

    assert result["ok"] is False
    assert "threshold.low_usage_mode" in result["validation"]["error"]


def test_apply_does_not_call_reload_when_validation_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    called = False

    def reload_callback():
        nonlocal called
        called = True
        return {"ok": True}

    result = apply_config_update(config_path, {"updates": {"alert.cooldown_minutes": -1}}, reload_callback)

    assert result["ok"] is False
    assert called is False
    assert not list(tmp_path.glob("*.bak-*"))


def test_profile_preview_generates_real_training_config_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)

    result = preview_profile_update(config_path, "training")
    changes = {change["path"]: change["new_value"] for change in result["changes"]}

    assert result["ok"] is True
    assert changes["scenario_profile"] == "training"
    assert changes["threshold.usage_percent"] == 20
    assert changes["threshold.idle_minutes"] == 10
    assert changes["threshold.no_process_minutes"] == 5
    assert changes["threshold.low_usage_mode"] == "any"
    assert "scenario_messages.low_usage_hint" in changes
    assert "dashboard.auth.token" not in changes


def test_profile_apply_backs_up_and_preserves_security_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  host: "127.0.0.1"
  auth:
    enabled: true
    token: secret-token
notify:
  control:
    enabled: false
  webhook:
    enabled: false
    url: https://example.com/secret
threshold:
  usage_percent: 99
""",
        encoding="utf-8",
    )

    result = apply_profile_update(config_path, "inference", lambda: {"ok": True})
    text = config_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert "scenario_profile: inference" in text
    assert "usage_percent: 10" in text
    assert "token: secret-token" in text
    assert "enabled: false" in text
    assert "https://example.com/secret" in text
    assert Path(result["backup_path"]).exists()


def test_profile_apply_rolls_back_on_reload_failure(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    before = config_path.read_text(encoding="utf-8")

    def fail_reload():
        raise RuntimeError("reload failed")

    result = apply_profile_update(config_path, "training", fail_reload)

    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert config_path.read_text(encoding="utf-8") == before


def test_user_profile_template_preview_and_unknown_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
dashboard:
  auth:
    enabled: false
profile_templates:
  llm_serving:
    threshold:
      usage_percent: 10
      idle_minutes: 30
      no_process_minutes: 10
      low_usage_mode: all
    scenario_messages:
      low_usage_hint: low traffic
      no_process_hint: serving stopped
""",
        encoding="utf-8",
    )

    result = preview_profile_update(config_path, "llm_serving")
    changes = {change["path"]: change["new_value"] for change in result["changes"]}

    assert result["ok"] is True
    assert changes["scenario_profile"] == "llm_serving"
    assert changes["scenario_messages.low_usage_hint"] == "low traffic"

    try:
        preview_profile_update(config_path, "missing")
    except Exception as exc:  # noqa: BLE001
        assert "unknown scenario_profile" in str(exc)
    else:
        raise AssertionError("expected unknown profile failure")
