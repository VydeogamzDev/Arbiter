-- M8 policy core: persisted user controls and breaker state (spec §15.3, §24).

CREATE TABLE control_override (
  scope TEXT NOT NULL,                 -- 'global' or a session id
  key TEXT NOT NULL,                   -- module:<flag> | controller | next_turn:<control>
  value_json TEXT NOT NULL,
  actor TEXT NOT NULL,                 -- user_tty | user_cli | agent
  reason TEXT NOT NULL DEFAULT '',
  set_at REAL NOT NULL,
  PRIMARY KEY (scope, key)
);

CREATE TABLE breaker_state (
  name TEXT PRIMARY KEY,
  opened_at REAL,
  trips INTEGER NOT NULL DEFAULT 0,
  last_reason TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL
);
