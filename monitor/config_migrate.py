from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

EnvEntry = tuple[str, str | None]

import yaml


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file must contain a YAML mapping: {path}")
    return data


def _merge_known_keys(template: Any, old: Any) -> Any:
    if isinstance(template, dict):
        if not isinstance(old, dict):
            return template
        merged: dict[str, Any] = {}
        for key, template_value in template.items():
            if key in old:
                merged[key] = _merge_known_keys(template_value, old[key])
            else:
                merged[key] = template_value
        return merged
    return old


def _parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '\"'}:
        return value[1:-1]
    return value


def read_env_file(path: Path) -> list[EnvEntry]:
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    entries: list[EnvEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            entries.append((line, None))
            continue
        assignment = stripped.removeprefix("export ")
        if "=" not in assignment:
            entries.append((line, None))
            continue
        key, raw_value = assignment.split("=", 1)
        key = key.strip()
        if not key:
            entries.append((line, None))
            continue
        entries.append((key, _parse_env_value(raw_value)))
    return entries


def build_merged_env(example_env_path: Path, old_env_path: Path) -> list[EnvEntry]:
    example_entries = read_env_file(example_env_path)
    old_values = {key: value for key, value in read_env_file(old_env_path) if value is not None}
    merged: list[EnvEntry] = []
    for key, example_value in example_entries:
        if example_value is None:
            merged.append((key, None))
            continue
        merged.append((key, old_values.get(key, example_value)))
    return merged


def _format_env_value(value: str) -> str:
    if value == "" or any(char.isspace() for char in value) or "#" in value:
        escaped = value.replace('\"', '\\"')
        return f'"{escaped}"'
    return value


def write_env_file(entries: list[EnvEntry], output_path: Path, force: bool = False) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(f"output already exists, pass --force to overwrite: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [entry if value is None else f"{entry}={_format_env_value(value)}" for entry, value in entries]
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="")


def write_merged_env(example_env_path: Path, old_env_path: Path, output_path: Path, force: bool = False) -> None:
    write_env_file(build_merged_env(example_env_path, old_env_path), output_path, force=force)


def build_merged_config(example_path: Path, old_config_path: Path) -> dict[str, Any]:
    example = _read_yaml(example_path)
    old_config = _read_yaml(old_config_path)
    return _merge_known_keys(example, old_config)


def write_merged_config(example_path: Path, old_config_path: Path, output_path: Path, force: bool = False) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(f"output already exists, pass --force to overwrite: {output_path}")
    merged = build_merged_config(example_path, old_config_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        yaml.safe_dump(merged, handle, allow_unicode=True, sort_keys=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge an old monitor config into the current config.example.yaml schema")
    parser.add_argument("--old", type=Path, default=Path("config.yaml"), help="Path to old config.yaml")
    parser.add_argument("--example", type=Path, default=Path("config.example.yaml"), help="Path to current config.example.yaml")
    parser.add_argument("--output", type=Path, default=Path("config.merged.yaml"), help="Path for generated config")
    parser.add_argument("--force", action="store_true", help="Overwrite output if it already exists")
    parser.add_argument("--old-env", type=Path, default=None, help="Path to old EnvironmentFile, for example /etc/default/gpu-monitor")
    parser.add_argument("--env-example", type=Path, default=Path("deploy/gpu-monitor.env.example"), help="Path to current env example file")
    parser.add_argument("--env-output", type=Path, default=None, help="Path for generated EnvironmentFile")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    write_merged_config(args.example, args.old, args.output, force=args.force)
    print(f"wrote merged config: {args.output}")
    if args.old_env is not None or args.env_output is not None:
        if args.old_env is None or args.env_output is None:
            raise SystemExit("--old-env and --env-output must be used together")
        write_merged_env(args.env_example, args.old_env, args.env_output, force=args.force)
        print(f"wrote merged env: {args.env_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())