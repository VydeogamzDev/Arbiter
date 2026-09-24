-- 0003: operational task state (M3) and the finish ledger (M4).

-- Current per-session controller state (mutable; the event log remains the audit record).
CREATE TABLE session_state (
  session_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  goal_epoch INTEGER NOT NULL DEFAULT 0,
  intent_count INTEGER NOT NULL DEFAULT 0,       -- ordinal of the latest intent row
  epoch_start_ordinal INTEGER NOT NULL DEFAULT 0, -- first intent ordinal of the current epoch
  candidate_ordinal INTEGER,                     -- pending candidate epoch opened by this intent (§6.3.1)
  epoch_confirmed_at REAL,
  epoch_reason TEXT,
  processed_seq INTEGER NOT NULL DEFAULT 0,      -- engine progress through event_log for this session
  hook_intents INTEGER NOT NULL DEFAULT 0,       -- intents seen via hooks (then transcript prompts are ignored)
  cwd TEXT,
  repo_json TEXT,
  baseline_id TEXT,
  stop_blocks INTEGER NOT NULL DEFAULT 0,        -- block-mode stops used in the current epoch
  integrity_json TEXT,
  flags_json TEXT NOT NULL DEFAULT '{}',          -- small per-session engine flags
  last_ledger_hash TEXT,
  last_injected_hash TEXT,
  updated_at REAL NOT NULL
);

-- Immutable user intent (spec §6.3): one row per user turn. thread_id holds the session id.
ALTER TABLE intent ADD COLUMN ordinal INTEGER;
ALTER TABLE intent ADD COLUMN source TEXT;
CREATE TRIGGER intent_append_only BEFORE UPDATE ON intent
BEGIN
  SELECT RAISE(ABORT, 'intent is append-only');
END;
CREATE TRIGGER intent_delete_via_retention BEFORE DELETE ON intent
WHEN (SELECT open FROM retention_gate WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT, 'intent rows may only be deleted by retention');
END;
CREATE INDEX intent_thread ON intent(thread_id, ordinal);

-- Observed facts with evidence origin (spec §6.4.1). Revisable (e.g. an exit code joined later
-- from the transcript); the raw events stay in event_log.
CREATE TABLE fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  goal_epoch INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL,           -- test_run | command_run | file_change | error | integrity_alert | loop_alert | usage
  origin TEXT NOT NULL CHECK (origin IN ('arbiter_observed', 'host_reported', 'agent_asserted')),
  subject TEXT,                 -- normalized command / path / fingerprint
  status TEXT,                  -- pass | fail | unknown | n/a
  tool_use_id TEXT,
  data_json TEXT NOT NULL DEFAULT '{}',
  source_seq INTEGER,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX fact_session ON fact(session_id, kind, created_at);
CREATE UNIQUE INDEX fact_tool_use ON fact(session_id, kind, tool_use_id) WHERE tool_use_id IS NOT NULL;

-- Explicit user decisions about contracts (waive / confirm a manual recipe).
CREATE TABLE contract_decision (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('waive', 'confirm', 'reject')),
  by_whom TEXT NOT NULL DEFAULT 'user',
  note TEXT,
  created_at REAL NOT NULL
);

-- Trusted .arbiter/verify.yaml files (hash approved by the user; agents can't approve).
CREATE TABLE verify_trust (
  path TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL,
  trusted_at REAL NOT NULL
);

-- Finish ledger (spec §12.5): one row per evaluated completion claim or finish check.
CREATE TABLE finish_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  goal_epoch INTEGER NOT NULL,
  created_at REAL NOT NULL,
  trigger TEXT NOT NULL,        -- stop_hook | finish_check | cli
  claim TEXT NOT NULL,          -- claim | not_claim | uncertain
  verdict TEXT NOT NULL,        -- verified | unverified | not_gated
  mode TEXT NOT NULL,           -- annotate | block
  blocked INTEGER NOT NULL DEFAULT 0,
  missing_json TEXT NOT NULL DEFAULT '[]',
  contracts_json TEXT NOT NULL DEFAULT '[]',
  ledger_text TEXT NOT NULL
);
CREATE INDEX finish_ledger_session ON finish_ledger(session_id, created_at);

-- Engine cursor over event_log (global; per-session progress lives in session_state).
CREATE TABLE engine_cursor (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  seq INTEGER NOT NULL DEFAULT 0
);
INSERT INTO engine_cursor (id, seq) VALUES (1, 0);

CREATE INDEX contract_thread ON contract(thread_id, goal_epoch);
