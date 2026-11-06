CREATE TABLE IF NOT EXISTS publication_drafts (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), content_revision INTEGER NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('simulation','live')), config_json TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_catalog (
 scope TEXT NOT NULL CHECK(scope IN ('simulation','live')), account_id TEXT NOT NULL,
 account_json TEXT NOT NULL, category_json TEXT, checked_at TEXT NOT NULL,
 PRIMARY KEY(scope,account_id)
);
CREATE TABLE IF NOT EXISTS publication_commands (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES publication_runs(id),
 job_id TEXT NOT NULL REFERENCES jobs(id), scope TEXT NOT NULL CHECK(scope IN ('simulation','live')),
 action TEXT NOT NULL CHECK(action IN ('submit','reconcile')),
 state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','COMPLETE','FAILED')),
 error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_publication_command_per_job
 ON publication_commands(job_id) WHERE state IN ('QUEUED','RUNNING');
