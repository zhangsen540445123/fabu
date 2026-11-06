CREATE TABLE IF NOT EXISTS upload_attempts (
 id TEXT PRIMARY KEY,
 scope TEXT NOT NULL CHECK(scope IN ('simulation','live')),
 resource TEXT NOT NULL CHECK(resource IN ('video','cover')),
 sha256 TEXT NOT NULL,
 snapshot_digest TEXT NOT NULL,
 source_video_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('SUBMITTING','COMPLETE','UNKNOWN')),
 receipt_cipher BLOB,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(scope,resource,sha256)
);
CREATE INDEX IF NOT EXISTS unresolved_uploads ON upload_attempts(scope,source_video_id,state);
