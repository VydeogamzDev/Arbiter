-- 0002: client observations, watcher parse context (M2).

ALTER TABLE watcher_offset ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';

-- Evidence that a client surface actually works (spec §4.2): an MCP session from the client,
-- a hook event over a given surface, a transcript parsed. The probe derives verified tiers from it.
CREATE TABLE client_observation (
  client_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('mcp_session', 'hook', 'transcript', 'capability')),
  detail TEXT NOT NULL DEFAULT '',
  first_at REAL NOT NULL,
  last_at REAL NOT NULL,
  count INTEGER NOT NULL DEFAULT 1,
  info_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (client_id, kind, detail)
);
