"""Config loading and validation.

Rules:
- ``defaults.yaml`` is the schema of record: every key a user may set exists there, and the
  default's type is the key's type (ints are accepted where floats are expected).
- User overrides are deep-merged; unknown keys and type mismatches are errors, never ignored.
- A small table of constraints adds enums and ranges on top of the type check.
- ``features`` is validated against the flag registry, not against defaults.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.paths import ArbiterPaths, get_paths


class ConfigError(ValueError):
    """Raised when a configuration file or override fails validation."""


# Enum and range constraints layered over the type check. Keys are dotted paths.
ENUMS: dict[str, set[Any]] = {
    "completion.gate_mode": {"annotate", "block"},
    "privacy.project_scope": {"all_except_excluded", "allow_list"},
    "setup.scope": {"user"},
    "ipc.tcp_fallback_bind": {"loopback"},
    "daemon.ipc": {"auto", "pipe", "unix", "tcp"},
    "reasoning.hosted_client_mode": {"advisory"},
    "state.epoch_confirmation": {"explicit_or_rule"},
}
POSITIVE: set[str] = {
    "hooks.telemetry_deadline_ms", "hooks.telemetry_p95_budget_ms", "hooks.gating_deadline_ms",
    "hooks.gating_p95_budget_ms", "hooks.max_injected_tokens", "storage.storage_cap_gb", "storage.max_payload_kb",
    "privacy.raw_payload_retention_days", "privacy.ledger_and_metrics_retention_days",
    "diagnostics.log_rotation_mb", "diagnostics.debug_mode_expiry_hours", "semif.timeout_ms",
    "semif.queue_capacity", "hosts.default_deadline_ms",
}
# Keys that exist but may hold either a scalar or a structured value.
FREE_FORM: set[str] = {"features"}


def load_defaults() -> dict[str, Any]:
    text = resources.files("arbiter_agent.config").joinpath("defaults.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ConfigError("packaged defaults.yaml is not a mapping")
    return data


def _type_ok(default: Any, value: Any) -> bool:
    if default is None:
        return True
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return type(default) is type(value)


def _merge(defaults: dict[str, Any], override: Mapping[str, Any], prefix: str, errors: list[str]) -> dict[str, Any]:
    out = copy.deepcopy(defaults)
    for key, value in override.items():
        path = f"{prefix}{key}"
        if key not in defaults:
            errors.append(f"unknown key '{path}'")
            continue
        base = defaults[key]
        if path in FREE_FORM:
            if not isinstance(value, dict):
                errors.append(f"'{path}' must be a mapping")
            else:
                out[key] = {**(base or {}), **value}
            continue
        if isinstance(base, dict) and base:
            if not isinstance(value, dict):
                errors.append(f"'{path}' must be a mapping, got {type(value).__name__}")
                continue
            out[key] = _merge(base, value, f"{path}.", errors)
            continue
        if not _type_ok(base, value):
            errors.append(f"'{path}' must be {type(base).__name__}, got {type(value).__name__}")
            continue
        out[key] = copy.deepcopy(value)
    return out


def _constraints(data: dict[str, Any], errors: list[str]) -> None:
    def get(path: str) -> Any:
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    for path, allowed in ENUMS.items():
        v = get(path)
        if v is not None and v not in allowed:
            errors.append(f"'{path}' must be one of {sorted(map(str, allowed))}, got {v!r}")
    for path in POSITIVE:
        v = get(path)
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            errors.append(f"'{path}' must be > 0, got {v!r}")
    flags = data.get("features") or {}
    from arbiter_agent.flags import REGISTRY

    for name, val in flags.items():
        if name not in REGISTRY:
            errors.append(f"unknown feature flag 'features.{name}'")
        elif not isinstance(val, bool):
            errors.append(f"'features.{name}' must be bool")


@dataclass(frozen=True)
class Config:
    data: dict[str, Any]
    source: Path | None = None
    paths: ArbiterPaths = field(default_factory=get_paths)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name)
        return dict(value) if isinstance(value, dict) else {}


def build_config(override: Mapping[str, Any] | None, *, source: Path | None = None,
                 paths: ArbiterPaths | None = None) -> Config:
    errors: list[str] = []
    merged = _merge(load_defaults(), override or {}, "", errors)
    _constraints(merged, errors)
    if errors:
        where = f" in {source}" if source else ""
        raise ConfigError(f"invalid configuration{where}:\n  - " + "\n  - ".join(errors))
    return Config(data=merged, source=source, paths=paths or get_paths())


def load_config(paths: ArbiterPaths | None = None, extra: Mapping[str, Any] | None = None) -> Config:
    """Load defaults + ``<config dir>/config.yaml`` (if present) + optional extra overrides."""
    paths = paths or get_paths()
    override: dict[str, Any] = {}
    source: Path | None = None
    if paths.config_file.exists():
        source = paths.config_file
        loaded = yaml.safe_load(paths.config_file.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{source} must contain a mapping")
        override = loaded
    if extra:
        override = _merge_raw(override, extra)
    return build_config(override, source=source, paths=paths)


def _merge_raw(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = _merge_raw(out[k], v)
        else:
            out[k] = v
    return out
