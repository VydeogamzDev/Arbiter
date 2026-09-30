"""System 1: a cheap, fast model beside the agent's model (opt-in, off by default).

The agent's model (Codex on gpt-6-sol at high effort, say) does the thinking; system 1 does the routine
work Arbiter can hand it: a brief of where a change goes before the agent explores (``brief``), a
second look at a diff before the agent stops (``judge``), a guess at how much effort a prompt needs
(``effort``). Measured 2026-09-29 on sol/high: reading and searching were 30-39% of its cost, its own
test runs 23-25% (mostly before Arbiter's result arrived), edits 27-30%.

Backends (``system1.backend``):

- ``codex``: ``codex exec`` on a cheap model (``system1.model``, gpt-6-luna) with the user's own Codex
  sign-in, or a separate one via ``system1.codex_home``. The stand-in until a local model runs.
- ``openai``: an OpenAI-compatible ``/chat/completions`` server (``system1.base_url``), e.g. a local
  llama.cpp or vLLM serving a fine-tuned K2 Horizon 3.7B / 7B.
- ``off``: nothing (the default).

Every call is bounded (``system1.timeout_s``) and fails soft: None, never an exception to the caller.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("arbiter.system1")
NW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class System1:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.backend = str(config.get("system1.backend", "off"))
        self.model = str(config.get("system1.model", "gpt-6-luna"))
        self.timeout_s = float(config.get("system1.timeout_s", 60.0))
        self.stats = {"calls": 0, "failures": 0, "seconds": 0.0, "input_chars": 0, "output_chars": 0}

    @property
    def enabled(self) -> bool:
        return self.backend in ("codex", "openai")

    def complete(self, prompt: str, *, max_tokens: int = 600, timeout_s: float | None = None) -> str | None:
        if not self.enabled:
            return None
        t0 = time.monotonic()
        self.stats["calls"] += 1
        self.stats["input_chars"] += len(prompt)
        try:
            out = (self._codex if self.backend == "codex" else self._openai)(prompt, max_tokens,
                                                                             timeout_s or self.timeout_s)
        except Exception as exc:
            log.warning("system1 call failed: %s", exc)
            out = None
        self.stats["seconds"] += time.monotonic() - t0
        if not out:
            self.stats["failures"] += 1
            return None
        self.stats["output_chars"] += len(out)
        return out.strip()

    # ------------------------------------------------------------------ backends
    def _codex(self, prompt: str, max_tokens: int, timeout_s: float) -> str | None:
        exe = self.config.get("system1.codex_bin") or os.environ.get("ARBITER_SYSTEM1_CODEX") or "codex"
        env = {k: v for k, v in os.environ.items() if not k.startswith("ARBITER_")}
        home = self.config.get("system1.codex_home")
        if home:
            env["CODEX_HOME"] = str(home)
        effort = str(self.config.get("system1.effort", "low"))
        with tempfile.TemporaryDirectory(prefix="arbiter-s1-") as tmp:
            # The prompt goes on stdin ("-"): a brief's files exceed Windows' 32,767-character command line.
            argv = [str(exe), "exec", "--ephemeral", "--json", "--ignore-user-config", "--skip-git-repo-check",
                    "-m", self.model, "-c", f'model_reasoning_effort="{effort}"', "-C", tmp, "-"]
            p = subprocess.run(argv, input="Answer from the text below only. Do not run commands or read files.\n\n"
                               + prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                               timeout=timeout_s, creationflags=NW)
        last = None
        for line in p.stdout.splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            item = o.get("item") or {}
            if o.get("type") == "item.completed" and item.get("type") == "agent_message":
                last = str(item.get("text") or "") or last
        return last

    def _openai(self, prompt: str, max_tokens: int, timeout_s: float) -> str | None:
        base = str(self.config.get("system1.base_url", "http://127.0.0.1:8080/v1")).rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError(f"system1.base_url must be http(s): {base}")
        body = json.dumps({"model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", data=body, method="POST",  # noqa: S310
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310 - configured local endpoint
            data = json.loads(r.read())
        return (data.get("choices") or [{}])[0].get("message", {}).get("content")


# ------------------------------------------------------------------ roles
BRIEF_PROMPT = """You help a coding agent start a task in a large repository. Below are the task and the files
Arbiter's index picked for it. Write a brief, at most {words} words, with only facts you can see in these files:
1. Where the change goes: file and function or class (with line numbers when shown).
2. Existing code to reuse or copy the pattern of (helpers, a sibling function doing the same thing).
3. Tests: the test file that covers this code and how its tests are written.
4. Anything easy to miss (an export to update, a docstring convention, an edge case the task names).
If the files don't show something, say so rather than guess.

TASK:
{task}

FILES:
{files}
"""

JUDGE_PROMPT = """You review a coding agent's change before it tells the user it's done. Compare the change to
what the user asked. Reply with exactly `OK` if it does everything asked. Otherwise reply with up to three short
numbered problems, each a concrete thing the user asked that the change doesn't do or gets wrong (a missing
requirement, a wrong edge case the request names, an incomplete fix). Don't comment on style, naming or tests
that aren't asked for.

WHAT THE USER ASKED (in order):
{requests}

THE CHANGE (git diff):
{diff}

LATEST TEST RESULT: {tests}
"""

EFFORT_PROMPT = """A coding agent will work on the request below in a repository. How much reasoning effort does it
need? Answer with one word.
`low`: the request says what to build or change and where (a named function, option, argument, helper, export or
test), even if the code is long or the change touches a few related functions. Most requests are `low`.
`high`: the cause is unknown and must be found (something "sometimes" gives wrong results, a failure with no
location), the request needs a design choice or a new algorithm, or it has a performance, concurrency or
security requirement.

REQUEST:
{request}
"""


def parse_judge(text: str | None) -> list[str] | None:
    """The judge's problems, [] for OK, None when there's no usable answer."""
    if not text:
        return None
    t = text.strip()
    if t.upper().startswith("OK"):
        return []
    problems = [ln.strip() for ln in t.splitlines() if ln.strip()[:2].rstrip(".):").isdigit()]
    return problems[:3] or [t[:400]]


def parse_effort(text: str | None) -> str | None:
    if not text:
        return None
    w = text.strip().lower()
    return "high" if w.startswith("high") else "low" if w.startswith("low") else None


def write_debug(path: Path | None, role: str, prompt: str, answer: str | None) -> None:
    if path is None:
        return
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "role": role, "prompt": prompt[:4000], "answer": answer}) + "\n")
    except OSError:
        pass
