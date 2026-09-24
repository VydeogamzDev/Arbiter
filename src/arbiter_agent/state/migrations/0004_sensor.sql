-- 0004: semantic sensor shadow log (M7.7). One row per shadow judgment, next to the rule decision
-- it would have informed. Never read by the gate; used for calibration and the tier benchmark.
CREATE TABLE sensor_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  session_id TEXT,
  family TEXT NOT NULL,
  question_hash TEXT NOT NULL,
  rule_decision TEXT,
  choice TEXT,
  abstain INTEGER NOT NULL,
  reason TEXT,
  margin REAL,
  spread REAL,
  backend TEXT,
  model TEXT,
  revision TEXT,
  latency_ms REAL,
  judgment_json TEXT NOT NULL,
  outcome TEXT
);
CREATE INDEX sensor_log_family ON sensor_log(family, created_at);
