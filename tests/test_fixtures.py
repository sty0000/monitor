from __future__ import annotations

from pathlib import Path


def test_collector_fixtures_exist() -> None:
    base = Path("tests/fixtures")
    expected = [
        base / "nvidia_smi" / "normal.csv",
        base / "nvidia_smi" / "na.csv",
        base / "nvidia_smi" / "reset.csv",
        base / "dcgmi" / "healthy.txt",
        base / "dcgmi" / "warning.txt",
    ]
    for path in expected:
        assert path.exists()
        assert path.read_text(encoding="utf-8").strip()
