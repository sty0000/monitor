from __future__ import annotations

import argparse
import copy
import difflib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EnvEntry = tuple[str, str | None]

import yaml

SECRET_KEYWORDS = {"token", "secret", "password", "webhook", "url", "chat_id"}


@dataclass
class ConfigMigrationPlan:
    old_config: dict[str, Any]
    example_config: dict[str, Any]
    merged_config: dict[str, Any]
    added_paths: list[str] = field(default_factory=list)
    removed_paths: list[str] = field(default_factory=list)
    preserved_unknown_paths: list[str] = field(default_factory=list)
    type_warnings: list[str] = field(default_factory=list)
    diff: str = ""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file must contain a YAML mapping: {path}")
    return data


def _is_secret_path(path: str) -> bool:
    lowered = path.lower()
    return any(keyword in lowered for keyword in SECRET_KEYWORDS)


def _redact(value: Any, path: str) -> Any:
    if _is_secret_path(path) and value not in {None, ""}:
        return "***REDACTED***"
    return value


def _leaf_paths(data: Any, prefix: str = "") -> set[str]:
    if isinstance(data, dict):
        paths: set[str] = set()
        for key, value in data.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.update(_leaf_paths(value, child))
        return paths or ({prefix} if prefix else set())
    return {prefix} if prefix else set()


def _redacted_copy(data: Any, prefix: str = "") -> Any:
    if isinstance(data, dict):
        return {key: _redacted_copy(value, f"{prefix}.{key}" if prefix else str(key)) for key, value in data.items()}
    if isinstance(data, list):
        return [_redacted_copy(value, prefix) for value in data]
    return _redact(data, prefix)


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


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


def _merge_with_report(template: Any, old: Any, path: str, plan: ConfigMigrationPlan) -> Any:
    if isinstance(template, dict):
        if not isinstance(old, dict):
            if old is not None:
                plan.type_warnings.append(f"{path or '<root>'}: expected mapping, using example default")
            plan.added_paths.extend(sorted(_leaf_paths(template, path)))
            return copy.deepcopy(template)
        merged: dict[str, Any] = {}
        for key, template_value in template.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in old:
                merged[key] = _merge_with_report(template_value, old[key], child_path, plan)
            else:
                merged[key] = copy.deepcopy(template_value)
                plan.added_paths.extend(sorted(_leaf_paths(template_value, child_path)))
        for key, old_value in old.items():
            if key not in template:
                child_path = f"{path}.{key}" if path else str(key)
                merged[key] = copy.deepcopy(old_value)
                plan.preserved_unknown_paths.extend(sorted(_leaf_paths(old_value, child_path)))
        return merged
    if isinstance(old, dict):
        plan.type_warnings.append(f"{path}: expected scalar/list, using example default")
        return copy.deepcopy(template)
    if old is None and template is not None:
        plan.added_paths.append(path)
        return copy.deepcopy(template)
    if old is not None and template is not None and not isinstance(old, type(template)):
        plan.type_warnings.append(f"{path}: old type {type(old).__name__} differs from example type {type(template).__name__}; keeping old value")
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


def build_migration_plan(example_path: Path, old_config_path: Path) -> ConfigMigrationPlan:
    example = _read_yaml(example_path)
    old_config = _read_yaml(old_config_path)
    plan = ConfigMigrationPlan(old_config=old_config, example_config=example, merged_config={})
    plan.merged_config = _merge_with_report(example, old_config, "", plan)
    example_paths = _leaf_paths(example)
    old_paths = _leaf_paths(old_config)
    plan.removed_paths = sorted(old_paths - example_paths)
    plan.preserved_unknown_paths = sorted(set(plan.preserved_unknown_paths))
    plan.added_paths = sorted(set(plan.added_paths) - old_paths)
    old_yaml = _dump_yaml(_redacted_copy(old_config))
    merged_yaml = _dump_yaml(_redacted_copy(plan.merged_config))
    plan.diff = "".join(
        difflib.unified_diff(
            old_yaml.splitlines(True),
            merged_yaml.splitlines(True),
            fromfile="current config.yaml (redacted)",
            tofile="upgraded config.yaml (redacted)",
        )
    )
    return plan


def render_migration_preview(plan: ConfigMigrationPlan) -> str:
    lines = ["Config migration preview", ""]
    lines.append("Added fields:")
    lines.extend(f"- {path}" for path in plan.added_paths[:200])
    if not plan.added_paths:
        lines.append("- none")
    lines.append("")
    lines.append("Preserved unknown fields:")
    lines.extend(f"- {path}" for path in plan.preserved_unknown_paths[:200])
    if not plan.preserved_unknown_paths:
        lines.append("- none")
    lines.append("")
    lines.append("Deprecated / not in example fields:")
    lines.extend(f"- {path}" for path in plan.removed_paths[:200])
    if not plan.removed_paths:
        lines.append("- none")
    lines.append("")
    lines.append("Type warnings:")
    lines.extend(f"- {warning}" for warning in plan.type_warnings[:200])
    if not plan.type_warnings:
        lines.append("- none")
    lines.append("")
    lines.append("Redacted YAML diff:")
    lines.append(plan.diff or "(no diff)")
    return "\n".join(lines).rstrip() + "\n"


def write_migration_output(plan: ConfigMigrationPlan, output_path: Path, force: bool = False) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(f"output already exists, pass --force to overwrite: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_dump_yaml(plan.merged_config), encoding="utf-8", newline="")


def apply_migration(plan: ConfigMigrationPlan, config_path: Path, backup_dir: Path | None = None) -> Path:
    backup_root = backup_dir or config_path.parent
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"{config_path.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
    tmp_path = config_path.with_name(f".{config_path.name}.upgrade-{os.getpid()}.tmp")
    shutil.copy2(config_path, backup_path)
    try:
        tmp_path.write_text(_dump_yaml(plan.merged_config), encoding="utf-8", newline="")
        os.replace(tmp_path, config_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        shutil.copy2(backup_path, config_path)
        raise
    return backup_path


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
    parser.add_argument("--dry-run", action="store_true", help="Print redacted schema diff preview without writing files")
    parser.add_argument("--apply", action="store_true", help="Back up --old and replace it with the upgraded config")
    parser.add_argument("--backup-dir", type=Path, default=None, help="Directory for --apply backups; defaults to config directory")
    parser.add_argument("--force", action="store_true", help="Overwrite output if it already exists")
    parser.add_argument("--old-env", type=Path, default=None, help="Path to old EnvironmentFile, for example /etc/default/gpu-monitor")
    parser.add_argument("--env-example", type=Path, default=Path("deploy/gpu-monitor.env.example"), help="Path to current env example file")
    parser.add_argument("--env-output", type=Path, default=None, help="Path for generated EnvironmentFile")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = build_migration_plan(args.example, args.old)
    preview = render_migration_preview(plan)
    if args.dry_run:
        print(preview, end="")
        return 0
    if args.apply:
        backup_path = apply_migration(plan, args.old, backup_dir=args.backup_dir)
        print(f"applied upgraded config: {args.old}")
        print(f"backup: {backup_path}")
        return 0
    write_migration_output(plan, args.output, force=args.force)
    print(preview, end="")
    print(f"wrote upgraded config: {args.output}")
    if args.old_env is not None or args.env_output is not None:
        if args.old_env is None or args.env_output is None:
            raise SystemExit("--old-env and --env-output must be used together")
        write_merged_env(args.env_example, args.old_env, args.env_output, force=args.force)
        print(f"wrote merged env: {args.env_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
