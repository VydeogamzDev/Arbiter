# 0030 — M8 policy core: one breaker board, fail modes as data, authority-checked controls

- Status: Accepted
- Date: 2026-09-24
- Spec: §7.7, §15.2–§15.5, §18.6, §24
- Builds on: [0009](0009-circuit-breakers-before-automation.md)

## Context
Decision 0009 requires the decision cascade and full circuit breakers before any automatic stage (M9 onward). M4 shipped three gate breakers. M7 added per-family sensor breakers with no way to see or reset them.

## Decisions
1. **Fail modes are data** (`policy/fail_modes.py`). The §18.6 matrix is a table of components with their mode (`fail_open`, `fail_conservative`, `fail_closed`, `abstain`) and behavior. Every breaker names a component, and status and the fault suite read the same table.
2. **One breaker board** (`policy/circuit_breaker.py`). Every breaker is registered with a spec (kind, component, threshold, window, cooldown, recovery window, latched, integrity, and the feature flags it forces off):
   - kinds: `hook_latency`, `gate_errors`, `parser:<runner>`, `false_complete:<session>`, `client:<id>`, `schema_miss:<id>`, `controller`, `retrieval`, `semif:<family>`, `semif_parity`, `stale_result`, `context_restore`, `learned_policy`;
   - **recovery window:** after the cooldown a breaker goes half-open and its forced-off flags are lifted so trial work can run. One failure reopens it; it closes only after N consecutive clean outcomes (§15.3);
   - **latched breakers** (false completion, stale speculation result, context restore failure, sensor parity drift, learned-policy loss) never cool down. Only a manual reset after inspection closes them;
   - open and latched state is persisted (`breaker_state`) and restored at start, so a restart can't clear an incident. Trips, closes and resets are audited as `internal.breaker` events.
3. **Breakers wired this milestone:**
   - **Client adapters:** the engine's `respond()` wraps every hook response. Errors count against `client:<id>`, and while it's open that client's hooks pass through unchanged. Inbound dialect errors record the raw payload and count too.
   - **Schema miss:** unrecognized hook events count per client.
   - **Controller:** engine catch-up errors and hook responses that overrun their deadline count. Open, it sheds status injection and the sensor.
   - **Retrieval:** index errors count, and while the breaker is open retrieval tools refuse with "use your own search tools" (fail open).
   - **False completion:** a verified stop whose contracts later fail, or turn flaky, in the same epoch with no code change latches the session's breaker. From then on the gate can't verify that session until reset.
   - **Sensor families** now live on the board, so they're visible and resettable.
4. **Cascade** (`policy/cascade.py`): the rule decides when decisive. Otherwise the sensor may recommend only when the family is routed (`semif.route_families`, empty until benchmarks back it), the judgment is stable, its breaker is closed, and the decision isn't high-consequence. Otherwise it abstains and the rule's conservative default applies.
5. **Conflict priority** (`policy/constraints.py`, `conflict_resolution.py`): the §15.5 levels as an enum. The highest priority wins, and upstream safety is absolute. **Evidence floors:** only integrity-level evidence can move a verdict or contract status toward `verified`/`pass`. A user's acceptance (`unverified_accepted`) or waiver is recorded as a decision, never as evidence.
6. **Authority** (`policy/authority.py`). The daemon derives the actor from the connection's hello component (`cli` or anything else), never from request parameters:
   - agents (MCP) may only request one-turn optimization bypasses (`next_turn:*`) for their own session;
   - the user CLI may switch optimization modules and reset optimization breakers;
   - switching off integrity modules, turning the controller off, and resetting integrity breakers need an interactive confirmation (`cli_tty`, as with `arbiter verify --trust`).

   The token is shared by all local components, so this guards against the agent's own tool calls, not against a local process impersonating the CLI. That matches the IPC trust model (decision 0005).
7. **Budgets and shedding** (`policy/budgets.py`, `overload.py`):
   - hard per-session, per-turn caps (sensor calls, injected tokens);
   - three shedding classes: integrity is never shed; foreground optimization is shed at 85% queue fill or when the latency breaker opens; background work (sensor shadow scoring) is shed at 50% fill or when p95 passes 80% of budget.
8. **Controls** (`ui/overrides.py`, `control_override`):
   - module on/off (global), `controller` on/off (global or per session: hooks return baseline behavior while events are still recorded), and `next_turn:*` bypasses;
   - `arbiter control`, `arbiter breakers [reset NAME]`, and the MCP tool `arbiter_controls` (list, plus request for the bypasses);
   - flags gained a user layer between config and breaker force-off: breakers still win.
9. **Fault injection** (`eval/fault_injection.py`, `arbiter eval --faults`). Every breaker kind has a scenario that injects its fault and checks the trip and the component's fail-mode behavior. The four breakers whose modules don't exist yet are driven through the board and checked for latching, flag forcing and reset, and are marked synthetic. The suite fails if any spec lacks a scenario.

## Consequences
- The M8 exit is met in tests: a 100% trip rate across 13 breaker kinds, optimization fails open, integrity fails conservative, and shims never block the host (`tests/test_policy.py`).
- "Lower controller cost without quality regression" becomes measurable once M9's advisory stages exist. M8 adds the budgets and shedding that bound it.
- M9 onward must register any new automatic module's breaker spec and fault scenario before shipping it; the fault suite enforces this.
