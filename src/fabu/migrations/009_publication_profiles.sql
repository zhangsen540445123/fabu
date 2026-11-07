CREATE TABLE IF NOT EXISTS publication_profiles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('simulation','live')),
 config_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_handoffs (
 job_id TEXT NOT NULL REFERENCES jobs(id), content_revision INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('PREPARING','QUEUED','PAUSED')),
 error_code TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(job_id,content_revision)
);
