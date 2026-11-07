CREATE TABLE IF NOT EXISTS publication_job_authorizations (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), policy_id TEXT NOT NULL,
 spec_digest TEXT NOT NULL, authorized_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_daily_reservations (
 day TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id), target_id TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(day,job_id,target_id)
);
CREATE TABLE IF NOT EXISTS workflow_preset_default (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 preset_version_id TEXT NOT NULL REFERENCES preset_versions(id)
);
