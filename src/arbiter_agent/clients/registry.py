"""Profile registry: built-in profiles plus user profiles under ``<config>/profiles/``."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from arbiter_agent.clients.profile_schema import Profile, ProfileError, parse_profile


@dataclass
class Registry:
    profiles: dict[str, Profile] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def get(self, pid: str) -> Profile:
        if pid not in self.profiles:
            raise KeyError(f"unknown client '{pid}' (known: {', '.join(sorted(self.profiles))})")
        return self.profiles[pid]

    def installable(self) -> list[Profile]:
        return [p for p in self.profiles.values() if p.mcp.get("format") != "print_only"]


def load_registry(user_dir: Path | None = None) -> Registry:
    reg = Registry()
    pkg = resources.files("arbiter_agent.clients").joinpath("profiles")
    for entry in sorted(pkg.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".yaml"):
            try:
                p = parse_profile(yaml.safe_load(entry.read_text(encoding="utf-8")))
                reg.profiles[p.id] = p
            except (ProfileError, yaml.YAMLError) as exc:
                reg.errors.append(f"builtin {entry.name}: {exc}")
    if user_dir and user_dir.is_dir():
        for f in sorted(user_dir.glob("*.yaml")):
            try:
                p = parse_profile(yaml.safe_load(f.read_text(encoding="utf-8")), source=str(f))
                reg.profiles[p.id] = p
            except (ProfileError, yaml.YAMLError, OSError) as exc:
                reg.errors.append(f"{f}: {exc} (ignored)")
    return reg
