# Monitor Compatibility Matrix

## Runtime

| Component | Supported / tested baseline | Notes |
| --- | --- | --- |
| Python | 3.10+ | Recommended Conda env: `conda create -n monitor python=3.10`. |
| OS | Ubuntu 20.04+ / DGX OS family | Windows is supported for unit tests and development only. |
| NVIDIA driver | Any version supported by local `nvidia-smi` | Use `python -m monitor.doctor` to verify the target host. |
| DCGM | Optional | Required only for DCGM health and extra telemetry. |
| Prometheus | 2.x compatible text scrape | Uses `/metrics` text exposition. |
| Grafana | 9.x+ recommended | Import `deploy/grafana-dashboard.example.json`. |

## Install Boundary

- The project does not install NVIDIA drivers, DCGM, Conda, or system packages automatically.
- Use `python -m monitor.init_config --print-conda-setup` to print environment commands.
- Use `python -m monitor.init_config --check-python-env` before installing systemd files.
- Use `python -m monitor.init_config --install-systemd` only after reviewing generated files.

## Upgrade Boundary

- Keep `config.yaml` and `logs/` during upgrades.
- Back up `config.yaml` before applying config edits or replacing examples.
- For schema differences, compare the current `config.yaml` with `config.example.yaml` and use Dashboard config preview for safe editable fields.
