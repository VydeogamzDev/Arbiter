-- M9 advisory decisions: every recommendation with its inputs, reasons and (later) its outcome.
-- The audit trail behind `arbiter advice` and Host Advisory API outcome reporting (spec §4.6, §8.8).

CREATE TABLE advisory_decision (
  decision_id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  kind TEXT NOT NULL,                  -- recommend_call | rerank | diff_risk | session_advice
  mode TEXT NOT NULL,                  -- shadow | advisory
  host TEXT,                           -- host id for Host Advisory API calls, else NULL
  partition_key TEXT NOT NULL,         -- per-project partition (normalized repository identity)
  session_id TEXT,
  request_json TEXT NOT NULL,
  response_json TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  outcome_json TEXT,
  outcome_at REAL
);
CREATE INDEX advisory_decision_partition ON advisory_decision(partition_key, created_at);
CREATE INDEX advisory_decision_session ON advisory_decision(session_id, created_at);
