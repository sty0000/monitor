from __future__ import annotations

import argparse
from pathlib import Path

from .service import print_once, run_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPU usage monitor with alerting")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to YAML config file")
    parser.add_argument("--once", action="store_true", help="Collect one sample and print JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.once:
        print_once(args.config)
        return 0
    return run_agent(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
