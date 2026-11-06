CREATE TABLE IF NOT EXISTS publication_runs (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), content_revision INTEGER NOT NULL,
 snapshot_digest TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('simulation','live')),
 state TEXT NOT NULL CHECK(state IN ('NEW','ACTIVE','PAUSED','COMPLETE')),
 reason TEXT, stage TEXT, owner_worker_id TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(scope,snapshot_digest)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_publication_run ON publication_runs(state) WHERE state='ACTIVE';
CREATE INDEX IF NOT EXISTS publication_job_runs ON publication_runs(job_id,content_revision);
CREATE TABLE IF NOT EXISTS publication_source_claims (
 scope TEXT NOT NULL, video_id TEXT NOT NULL, target_id TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES publication_runs(id),
 PRIMARY KEY(scope,video_id,target_id)
);
CREATE TABLE IF NOT EXISTS publication_attempt_links (
 attempt_id TEXT PRIMARY KEY REFERENCES target_attempts(id),
 run_id TEXT NOT NULL REFERENCES publication_runs(id),
 ordinal INTEGER NOT NULL, receipt_json TEXT,
 definitely_not_submitted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS publication_attempts_by_run ON publication_attempt_links(run_id,ordinal);
CREATE TABLE IF NOT EXISTS publication_reconcile_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES target_attempts(id),
 result_json TEXT NOT NULL, checked_at TEXT NOT NULL
);
