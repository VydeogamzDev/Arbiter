"""Hardware detection and tier planning for ``arbiter semif enable`` (M7.6; decision 0026)."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Gpu:
    name: str
    vram_gb: float


@dataclass
class Plan:
    gpus: list[Gpu] = field(default_factory=list)
    encoder_available: bool = False          # gliner2 importable
    tier0: bool = True                        # the encoder runs on CPU, so always possible
    decoder: dict[str, Any] | None = None     # chosen decoder tier, if a GPU fits one
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_gpus() -> list[Gpu]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            try:
                gpus.append(Gpu(parts[0], round(float(parts[1]) / 1024, 1)))
            except ValueError:
                continue
    return gpus


def plan(config: Any, gpus: list[Gpu] | None = None) -> Plan:
    p = Plan(gpus=detect_gpus() if gpus is None else gpus)
    p.encoder_available = all(importlib.util.find_spec(m) is not None for m in ("gliner2", "torch"))
    if not p.encoder_available:
        p.notes.append("tier 0 needs the gliner2 package: `uv tool install 'arbiter-agent[encoder]'` "
                       "(pulls PyTorch; the model weights are 1.95 GB, downloaded on first use unless "
                       "semif.encoder.model points at a local folder)")
    vram = max((g.vram_gb for g in p.gpus), default=0.0)
    tiers = sorted(config.get("semif.model_tiers") or [], key=lambda t: float(t.get("min_vram_gb", 0)))
    fitting = [t for t in tiers if vram >= float(t.get("min_vram_gb", 0))]
    if fitting:
        p.decoder = dict(fitting[-1])
    elif p.gpus:
        p.notes.append(f"largest GPU has {vram} GB; the smallest decoder tier needs "
                       f"{tiers[0].get('min_vram_gb') if tiers else '?'} GB")
    else:
        p.notes.append("no NVIDIA GPU detected: decoder tiers stay off (rules + tier 0 only)")
    return p
