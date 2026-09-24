-- 0001: core schema (spec §16.2) + M1 infrastructure.
-- Tables for later milestones are created now so the audit schema is stable from day one.

CREATE TABLE client (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  profile_version INTEGER,
  client_version TEXT,
  verified_tiers_json TEXT NOT NULL DEFAULT '[]',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  detected_at REAL,
  last_probe_at REAL,
  drift_json TEXT
);

CREATE TABLE client_session (
  id TEXT PRIMARY KEY,                 -- '<client_id>:<native_session_id>'
  client_id TEXT NOT NULL,
  native_session_id TEXT NOT NULL,
  repo_id TEXT,
  cwd_hmac TEXT,
  thread_id TEXT,
  transcript_path TEXT,
  started_at REAL NOT NULL,
  last_event_at REAL NOT NULL,
  ended_at REAL,
  verified_tiers_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE (client_id, native_session_id)
);
CREATE INDEX client_session_last_event ON client_session(last_event_at);

CREATE TABLE install_manifest (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id TEXT NOT NULL,
  config_file TEXT NOT NULL,
  entry_path TEXT NOT NULL,
  entry_hash TEXT NOT NULL,
  backup_pointer TEXT,
  installed_at REAL NOT NULL,
  removed_at REAL
);

-- Content-addressed payload store. hash = HMAC-SHA256(install key, content) so identifiers
-- can't confirm guessable content (spec §16.3). Rows may be deleted by retention only.
CREATE TABLE blob (
  hash TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  truncated INTEGER NOT NULL DEFAULT 0,
  full_size INTEGER NOT NULL,
  created_at REAL NOT NULL,
  data BLOB NOT NULL
);

CREATE TABLE retention_gate (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  open INTEGER NOT NULL DEFAULT 0
);
INSERT INTO retention_gate (id, open) VALUES (1, 0);

CREATE TABLE event_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,         -- monotonic local sequence (spec §6.1)
  client_id TEXT NOT NULL,
  client_profile_version INTEGER,
  session_id TEXT,                               -- client_session.id
  native_session_id TEXT,
  surface TEXT NOT NULL CHECK (surface IN ('mcp', 'hook', 'http', 'transcript', 'driver', 'cli', 'internal')),
  thread_id TEXT,
  turn_id TEXT,
  goal_epoch INTEGER NOT NULL DEFAULT 0,
  state_version INTEGER NOT NULL DEFAULT 0,
  upstream_id TEXT,
  event_type TEXT NOT NULL,
  raw_hash TEXT NOT NULL,
  upstream_ts TEXT,
  received_at REAL NOT NULL,
  payload_pointer TEXT,                          -- blob.hash (no FK: blobs expire under retention)
  sensitivity_class TEXT NOT NULL DEFAULT 'normal',
  idempotency_key TEXT NOT NULL UNIQUE,          -- same raw event on the same surface
  dedupe_key TEXT,                               -- same logical occurrence across surfaces
  duplicate_of INTEGER REFERENCES event_log(seq),
  ingest_channel TEXT,
  attrs_json TEXT NOT NULL DEFAULT '{}'           -- small non-sensitive fields for replay (ids, flags)
);
CREATE INDEX event_log_session ON event_log(session_id, seq);
CREATE INDEX event_log_dedupe ON event_log(dedupe_key);
CREATE INDEX event_log_received ON event_log(received_at);

CREATE TRIGGER event_log_append_only BEFORE UPDATE ON event_log
BEGIN
  SELECT RAISE(ABORT, 'event_log is append-only');
END;

CREATE TRIGGER event_log_delete_via_retention BEFORE DELETE ON event_log
WHEN (SELECT open FROM retention_gate WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT, 'event_log rows may only be deleted by retention');
END;

CREATE TABLE watcher_offset (
  source_id TEXT PRIMARY KEY,                    -- '<client_id>:<normalized path>'
  path TEXT NOT NULL,
  file_identity TEXT,                            -- size/ctime/inode fingerprint to detect rotation
  offset INTEGER NOT NULL DEFAULT 0,
  parser TEXT NOT NULL,
  parser_version INTEGER NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE scope_skip (
  reason TEXT PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0,
  last_at REAL
);

CREATE TABLE thread (
  id TEXT PRIMARY KEY,
  client_id TEXT,
  created_at REAL NOT NULL,
  codex_version TEXT,
  model TEXT,
  integration_mode TEXT,
  current_goal_epoch INTEGER NOT NULL DEFAULT 0,
  current_state_version INTEGER NOT NULL DEFAULT 0,
  current_effort TEXT,
  controller_enabled INTEGER NOT NULL DEFAULT 1,
  circuit_breakers_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE checkpoint (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT REFERENCES thread(id),
  goal_epoch INTEGER, state_version INTEGER, timestamp REAL, phase TEXT,
  effort_before TEXT, effort_after TEXT, effort_update_outcome TEXT,
  loop_score REAL, risk_tags_json TEXT, context_epoch INTEGER,
  tool_surface_hash TEXT, retrieval_set_hash TEXT,
  controller_versions_json TEXT, sem_if_scores_json TEXT, policy_reason_json TEXT,
  cached_tokens INTEGER, input_tokens INTEGER, output_tokens INTEGER, latency_ms REAL, outcome_json TEXT
);

CREATE TABLE context_item (
  id TEXT PRIMARY KEY,
  thread_id TEXT, goal_epoch INTEGER, created_at REAL, type TEXT, source TEXT,
  token_count INTEGER, visibility_tier TEXT, sensitivity_class TEXT,
  current_relevance REAL, future_relevance REAL, reproducibility REAL,
  reconstruction_cost REAL, causal_importance REAL, unresolved_evidence INTEGER,
  archive_pointer TEXT, content_hash TEXT, superseded_by TEXT, metadata_json TEXT
);

CREATE TABLE context_edge (
  parent_id TEXT NOT NULL, child_id TEXT NOT NULL, relation_type TEXT NOT NULL,
  PRIMARY KEY (parent_id, child_id, relation_type)
);

CREATE TABLE intent (
  id TEXT PRIMARY KEY,
  thread_id TEXT, goal_epoch INTEGER, raw_text_pointer TEXT, timestamp REAL,
  supersedes_intent_id TEXT, user_visible_hash TEXT
);

CREATE TABLE contract (
  id TEXT NOT NULL, thread_id TEXT, goal_epoch INTEGER, version INTEGER NOT NULL DEFAULT 1,
  normalized_text TEXT, source_intent_ids_json TEXT, quotes_json TEXT, proposed_by TEXT, scope TEXT,
  status TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('pass', 'fail', 'unknown', 'waived')),
  mapping_confidence REAL, strength_flag TEXT, verification_recipe_json TEXT,
  min_evidence_origin TEXT, evidence_json TEXT, superseded_by TEXT,
  PRIMARY KEY (id, version)
);

CREATE TABLE session_baseline (
  id TEXT PRIMARY KEY,
  session_id TEXT, repo_identity_json TEXT, head TEXT, dirty INTEGER,
  test_file_hashes_json TEXT, harness_config_hashes_json TEXT, test_inventory_pointer TEXT, captured_at REAL
);

CREATE TABLE task_state_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT, goal_epoch INTEGER, state_version INTEGER, checkpoint_id INTEGER,
  state_json TEXT, provenance_json TEXT, consistency_status TEXT
);

CREATE TABLE tool_decision (
  checkpoint_id INTEGER, catalog_version TEXT, family TEXT, tool_id TEXT,
  score REAL, discoverable INTEGER, reason TEXT
);

CREATE TABLE retrieval_candidate (
  checkpoint_id INTEGER, repo_id TEXT, index_version TEXT, file_hash TEXT,
  repo_path TEXT, symbol TEXT, span TEXT, generator TEXT, base_rank INTEGER,
  semantic_score REAL, selected INTEGER, reason TEXT
);

CREATE TABLE diff_hunk (
  checkpoint_id INTEGER, file_path TEXT, file_hash TEXT, hunk_id TEXT,
  deterministic_tags_json TEXT, semantic_risk REAL,
  review_tier TEXT, tests_selected_json TEXT, verification_result_json TEXT
);

CREATE TABLE verification (
  checkpoint_id INTEGER, contract_id TEXT, evidence_type TEXT, evidence_origin TEXT, ingest_channel TEXT,
  parser_id TEXT, parser_version INTEGER, evidence_pointer TEXT, evidence_grade TEXT, status TEXT, confidence REAL
);

CREATE TABLE utility_decision (
  checkpoint_id INTEGER, policy_version TEXT, action_family TEXT,
  candidate_actions_json TEXT, action_propensities_json TEXT,
  predicted_outcomes_json TEXT, uncertainty_json TEXT,
  chosen_action TEXT, realized_outcome_json TEXT
);

CREATE TABLE speculative_action (
  id TEXT PRIMARY KEY, checkpoint_id INTEGER, goal_epoch INTEGER, base_state_version INTEGER,
  action_type TEXT, dependency_fingerprint TEXT, started_at REAL, completed_at REAL,
  hit INTEGER, invalidated INTEGER, cancellation_reason TEXT, cost_json TEXT, result_pointer TEXT
);

CREATE TABLE branch_run (
  id TEXT PRIMARY KEY, checkpoint_id INTEGER, goal_epoch INTEGER, base_state_version INTEGER, base_repo_hash TEXT,
  branch_type TEXT, hypothesis_id TEXT, sandbox_id TEXT, budget_json TEXT,
  status TEXT, outcome_json TEXT, winner INTEGER, cleanup_status TEXT
);
