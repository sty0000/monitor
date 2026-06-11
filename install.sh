#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

cat <<'MSG'
[monitor] Safe installer helper

This script does not install system packages, NVIDIA drivers, DCGM, or Conda.
Recommended environment setup:

  conda create -n monitor python=3.10
  conda activate monitor
  pip install -U pip
  pip install -r requirements.txt

Next checks:

  python -m monitor.init_config --check-python-env
  python -m monitor.init_config

To install systemd after reviewing generated files:

  python -m monitor.init_config --install-systemd

Rollback:

  python -m monitor.init_config --uninstall-systemd
MSG

if [[ "${1:-}" == "--run-init" ]]; then
  python -m monitor.init_config
fi
