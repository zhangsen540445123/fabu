CREATE TABLE IF NOT EXISTS media_previews (
 id TEXT PRIMARY KEY,
 preset_version_id TEXT NOT NULL REFERENCES preset_versions(id),
 manifest_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preview_approvals (
 id TEXT PRIMARY KEY,
 preview_id TEXT NOT NULL UNIQUE REFERENCES media_previews(id),
 semantic_digest TEXT NOT NULL,
 engine_version TEXT NOT NULL,
 approved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS approval_by_semantics ON preview_approvals(semantic_digest,engine_version);
CREATE TABLE IF NOT EXISTS processing_snapshots (
 job_id TEXT NOT NULL REFERENCES jobs(id),
 content_revision INTEGER NOT NULL,
 preset_version_id TEXT NOT NULL REFERENCES preset_versions(id),
 source_json TEXT NOT NULL,
 plan_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(job_id,content_revision)
);
