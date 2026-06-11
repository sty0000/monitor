# Monitor Release Checklist

## 发布前
- 更新 `monitor/__init__.py` 中的 `__version__`。
- 检查 `docs/compatibility-matrix.md` 是否仍符合目标 Python、Ubuntu/DGX OS、driver、DCGM、Prometheus/Grafana 版本。
- 运行 `python -m py_compile monitor/*.py tests/*.py`。
- 运行 `python -m pytest tests -q`。
- 运行 text-file-hygiene 扫描本次修改的文本文件。
- 运行 `python -m monitor.doctor --config config.example.yaml --env deploy/gpu-monitor.env.example --skip-network-checks --skip-systemd-checks --json`。


## Release artifacts
- Source archive: include `monitor/`, `tests/`, `deploy/`, `docs/`, `config.example.yaml`, `README.md`, `requirements.txt` and `install.sh`.
  Expected result: archive extracts without generated logs, local `config.yaml`, caches, checkpoints or secrets.
- Optional wheel: build only after full tests pass.
  Expected result: `python -m monitor.init_config --check-python-env` works in a fresh environment after install.
- Release notes: document version, compatibility matrix changes, migration notes, known limits and rollback command.
  Expected result: users can decide whether to upgrade without reading the source diff.

## Release smoke
- Compile: run `python -m py_compile` with an explicit file list for `monitor/*.py` and `tests/*.py`.
  Expected result: command exits 0.
- Tests: run `python -m pytest tests -q -p no:cacheprovider --basetemp=C:\tmp\monitor-pytest-release`.
  Expected result: all tests pass without using project-local pytest cache directories.
- Doctor: run `python -m monitor.doctor --config config.example.yaml --env deploy/gpu-monitor.env.example --skip-network-checks --skip-systemd-checks --json`.
  Expected result: JSON output is parseable and contains no secret values.
- Config migration: run `python -m monitor.config_migrate --current config.example.yaml --example config.example.yaml --dry-run`.
  Expected result: preview completes without writing files.
- Deploy examples: parse Prometheus/Grafana examples with PyYAML and `python -m json.tool`.
  Expected result: every example parses successfully.

## Target smoke record
- Environment: record hostname, OS/DGX OS, Python version, NVIDIA driver, CUDA runtime if present, DCGM version if present, GPU model and monitor version.
  Expected result: target environment can be compared with `docs/compatibility-matrix.md`.
- Service health: record `systemctl status gpu-monitor-dashboard --no-pager` and `/api/health` output.
  Expected result: service is active or the failure is explained with doctor output.
- Metrics: record `/metrics` availability and whether advanced telemetry metrics appear when DCGM/NVML is enabled.
  Expected result: missing deep telemetry does not break basic GPU metrics.
- Rollback drill: run uninstall command in a disposable target or staging machine and verify `config.yaml` and `logs/` remain.
  Expected result: rollback path is documented and does not remove user data.

## 部署验证
- `python -m monitor.init_config --check-python-env`。
- `python -m monitor.init_config --install-systemd`。
- `curl -i http://127.0.0.1:8093/api/health -H 'Authorization: Bearer <token>'`。
- `curl -i http://127.0.0.1:8093/metrics -H 'Authorization: Bearer <token>'`。

## 回滚
- `python -m monitor.init_config --uninstall-systemd`。
- 如需删除 systemd 文件：`python -m monitor.init_config --uninstall-systemd --remove-system-files`。
- 保留 `config.yaml` 和 `logs/`，不要默认删除用户数据。

## 安装边界
- `install.sh` 只打印安全安装建议；除非传入 `--run-init`，否则不写配置、不安装 systemd。
- 不提供 `curl | bash` 流程；发布说明应要求用户先下载、审查，再本地执行。
