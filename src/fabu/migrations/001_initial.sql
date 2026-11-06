CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL, content_revision INTEGER NOT NULL,
 state TEXT NOT NULL, spec_json TEXT NOT NULL, source_json TEXT, plan_digest TEXT,
 output_json TEXT, pause_reason TEXT, owner_worker_id TEXT, content_frozen INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_revisions (
 job_id TEXT NOT NULL REFERENCES jobs(id), content_revision INTEGER NOT NULL,
 spec_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id,content_revision)
);
CREATE TABLE IF NOT EXISTS stage_runs (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), content_revision INTEGER NOT NULL,
 stage TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS target_attempts (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), revision INTEGER NOT NULL,
 target_id TEXT NOT NULL, payload_digest TEXT NOT NULL, provider_task_id TEXT,
 state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
 event_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,created_at);
CREATE INDEX IF NOT EXISTS attempts_by_job ON target_attempts(job_id,created_at);
