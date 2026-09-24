"""Constraint levels (spec §15.5): lower number = higher priority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class Level(IntEnum):
    UPSTREAM_SAFETY = 1          # platform/upstream safety and permission policy
    USER_INSTRUCTION = 2         # explicit current user instruction within allowed policy
    CONTRACTS = 3                # active contracts, deterministic high-consequence requirements
    INTEGRITY = 4                # integrity / verification requirements
    EVIDENCE = 5                 # context / evidence preservation
    COVERAGE_FLOORS = 6          # diff-risk and retrieval/tool coverage floors
    HARD_BUDGETS = 7             # isolation, rate-limit, hard budget constraints
    BREAKERS = 8                 # circuit breakers and compatibility gates
    LEARNED_UTILITY = 9          # learned utility optimization
    REASONING_COST = 10          # reasoning cost optimization
    SEMIF_PREFERENCE = 11        # sensor preference among otherwise allowed options


@dataclass(frozen=True)
class Constraint:
    level: Level
    key: str                     # what it constrains, e.g. "gate_mode", "module:repo_index", "verdict"
    value: Any
    source: str                  # who asserted it (config, user_cli, breaker:gate_errors, semif, ...)
    reason: str = ""
