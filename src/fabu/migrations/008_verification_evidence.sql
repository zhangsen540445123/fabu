CREATE TABLE IF NOT EXISTS verification_evidence (
 id TEXT PRIMARY KEY,
 snapshot_digest TEXT NOT NULL,
 permit_id TEXT NOT NULL,
 kind TEXT NOT NULL,
 evidence_cipher BLOB NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS verification_by_snapshot ON verification_evidence(snapshot_digest,permit_id);
