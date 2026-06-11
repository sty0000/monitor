from pathlib import Path


def test_compatibility_matrix_documents_install_boundary() -> None:
    text = Path("docs/compatibility-matrix.md").read_text(encoding="utf-8")

    assert "Python" in text
    assert "DCGM" in text
    assert "does not install NVIDIA drivers" in text


def test_install_script_is_safe_helper() -> None:
    text = Path("install.sh").read_text(encoding="utf-8")

    assert "curl" not in text
    assert "apt install" not in text
    assert "--run-init" in text
    assert "python -m monitor.init_config --install-systemd" in text


def test_release_checklist_mentions_compatibility_and_rollback() -> None:
    text = Path("docs/release-checklist.md").read_text(encoding="utf-8")

    assert "docs/compatibility-matrix.md" in text
    assert "--uninstall-systemd" in text
    assert "install.sh" in text



def test_release_checklist_documents_target_smoke_and_artifacts() -> None:
    text = Path("docs/release-checklist.md").read_text(encoding="utf-8")

    assert "Release artifacts" in text
    assert "Target smoke record" in text
    assert "Expected result" in text
    assert "python -m monitor.doctor" in text
    assert "python -m monitor.config_migrate" in text
