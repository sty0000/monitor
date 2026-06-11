from __future__ import annotations

import re
from pathlib import Path


def _anchor(title: str) -> str:
    value = title.strip().lower()
    value = re.sub(r"[ `./]", "-", value)
    value = value.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def test_readme_internal_links_have_matching_headings() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    headings = {_anchor(match.group(1)) for match in re.finditer(r"^#+\s+(.+)$", text, flags=re.MULTILINE)}
    links = re.findall(r"\]\(#([^\)]+)\)", text)
    missing = [link for link in links if link not in headings]
    assert missing == []


def test_readme_documents_training_and_inference_templates() -> None:
    text = Path("README.md").read_text(encoding="utf-8")

    assert 'scenario_profile: "training"' in text
    assert 'scenario_profile: "inference"' in text
    assert "usage_percent" in text
    assert "idle_minutes" in text
    assert "no_process_minutes" in text
    assert "low_usage_mode" in text
    assert "推理低流量" in text
    assert "训练进程消失" in text


def test_readme_documents_profile_templates_as_config_source() -> None:
    text = Path("README.md").read_text(encoding="utf-8")

    assert "profile_templates" in text
    assert "scenario_messages" in text
    assert "运行时不会动态读取第二套" in text
    assert "threshold.*" in text
