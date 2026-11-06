CREATE TABLE IF NOT EXISTS asset_labels (
 asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
 display_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_operations (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), content_revision INTEGER NOT NULL,
 request_json TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT, error_code TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS operations_by_job ON media_operations(job_id,created_at);
