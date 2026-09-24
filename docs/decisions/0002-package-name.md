# 0002 — Distribution `arbiter-agent`, import `arbiter_agent`, command `arbiter`

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.5.1, §25

## Context
The PyPI name `arbiter` is taken by an unrelated data-handling library (version 1.1.2, `rastern/arbiter`), checked on 2026-09-24. `arbiter-agent`, `arbiter-cp`, and `agent-arbiter` were free.

## Decision
- The distribution is `arbiter-agent`, installed with `uv tool install arbiter-agent`.
- The import package is `arbiter_agent`, so it can't collide with the other library in a shared environment.
- The user-facing command stays `arbiter`.

## Consequences
- Documentation must use `arbiter-agent` for install commands and `arbiter` for everything else.
- If another tool on a user's PATH also provides an `arbiter` command, `arbiter doctor` should detect and report the shadowing.
