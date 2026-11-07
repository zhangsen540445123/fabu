-- Provider observations are deliberately separate from publication/job state.
-- One latest, sanitized observation per immutable attempt; scheduling is durable.
CREATE TABLE IF NOT EXISTS provider_observations (
 attempt_id TEXT PRIMARY KEY REFERENCES target_attempts(id),
 run_id TEXT NOT NULL REFERENCES publication_runs(id),
 account_id TEXT NOT NULL,
 provider_task_id TEXT NOT NULL,
 snapshot_digest TEXT NOT NULL,
 platform TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','running','success','failed','unknown')),
 checked_at TEXT,
 next_check_at TEXT,
 platform_content_id TEXT,
 reason TEXT NOT NULL,
 quality_status TEXT NOT NULL DEFAULT 'unverified' CHECK(quality_status='unverified'),
 expected_duration_s REAL,
 observed_duration_s REAL CHECK(observed_duration_s IS NULL),
 started_at TEXT NOT NULL,
 deadline_at TEXT NOT NULL,
 check_count INTEGER NOT NULL DEFAULT 0 CHECK(check_count >= 0),
 failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
 claim_token TEXT
);
CREATE INDEX IF NOT EXISTS provider_observations_due ON provider_observations(next_check_at);
CREATE INDEX IF NOT EXISTS provider_observations_run ON provider_observations(run_id,account_id);
