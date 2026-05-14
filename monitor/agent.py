from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .logging_utils import configure_logging
from .runtime_service import MonitorRuntimeService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPU usage monitor with alerting")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to YAML config file")
    parser.add_argument("--once", action="store_true", help="Collect one sample and print JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config.logging)

    if args.once:
        runtime = MonitorRuntimeService(args.config)
        sample = runtime.collector.collect_sample(config.monitor)
        print(json.dumps(sample, ensure_ascii=False, indent=2))
        return 0

    runtime = MonitorRuntimeService(args.config)
    runtime.start()
    try:
        while True:
            runtime.stop_event.wait(1)
            if runtime.stop_event.is_set():
                break
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

