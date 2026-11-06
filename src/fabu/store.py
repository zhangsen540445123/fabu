"""Short SQLite transactions; no transaction is held while external tools run."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fabu.domain import JobSnapshot, JobSpec, TargetAttempt
from fabu.redaction import redact_event

_TRANSITIONS = {
    "NEW": {"INSPECTING", "PAUSED", "FAILED", "CANCELED"},
    "INSPECTING": {"DOWNLOADING", "PAUSED", "FAILED", "CANCELED"},
    "DOWNLOADING": {"PROCESSING", "PAUSED", "FAILED", "CANCELED"},
    "PROCESSING": {"READY", "PAUSED", "FAILED", "CANCELED"},
    "READY": {"PUBLISHING", "PAUSED", "FAILED", "CANCELED"},
    "PUBLISHING": {"PAUSED", "FAILED", "CANCELED"},
    "PAUSED": {"NEW", "FAILED", "CANCELED"},
    "FAILED": {"NEW", "CANCELED"},
    "CANCELED": set(),
    "COMPLETED": set(),
}
_SIDE_EFFECT_STATES = {
    "SUBMITTING",
    "PROVIDER_ACCEPTED",
    "SUBMITTED",
    "SCHEDULED",
    "PUBLISHED",
    "UNKNOWN",
}


class StaleRevision(ValueError):
    pass


class InvalidTransition(ValueError):
    pass


class FrozenContent(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
                for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                    if db.execute(
                        "SELECT 1 FROM schema_migrations WHERE name=?", (migration.name,)
                    ).fetchone():
                        continue
                    statement = ""
                    for line in migration.read_text(encoding="utf-8").splitlines():
                        statement += line + "\n"
                        if sqlite3.complete_statement(statement):
                            db.execute(statement)
                            statement = ""
                    if statement.strip():
                        raise ValueError("Incomplete database migration")
                    db.execute("INSERT INTO schema_migrations VALUES (?)", (migration.name,))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _snapshot(row) -> JobSnapshot:
        if row is None:
            raise KeyError("Task does not exist")
        return JobSnapshot(
            id=row["id"],
            revision=row["revision"],
            content_revision=row["content_revision"],
            spec=JobSpec.model_validate_json(row["spec_json"]),
            source_manifest=json.loads(row["source_json"]) if row["source_json"] else None,
            plan_digest=row["plan_digest"],
            output_manifest=json.loads(row["output_json"]) if row["output_json"] else None,
            state=row["state"],
            pause_reason=row["pause_reason"],
            content_frozen=bool(row["content_frozen"]),
            owner_worker_id=row["owner_worker_id"],
        )

    @staticmethod
    def _event(db, job_id: str, event: dict):
        db.execute(
            "INSERT INTO events(job_id,event_json,created_at) VALUES (?,?,?)",
            (job_id, json.dumps(redact_event(event), ensure_ascii=False), _now()),
        )

    def create(self, spec: JobSpec) -> JobSnapshot:
        spec = JobSpec.model_validate(spec.model_dump())
        identifier, now = str(uuid4()), _now()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO jobs(id,revision,content_revision,state,spec_json,
                         created_at,updated_at) VALUES (?,1,1,'NEW',?,?,?)""",
                (identifier, spec.model_dump_json(), now, now),
            )
            db.execute(
                "INSERT INTO job_revisions VALUES (?,1,?,?)",
                (identifier, spec.model_dump_json(), now),
            )
            self._event(db, identifier, {"event": "created", "status": "NEW"})
        return self.get(identifier)

    def get(self, job_id: str) -> JobSnapshot:
        with self.connection() as db:
            return self._snapshot(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, limit: int = 100) -> list[JobSnapshot]:
        with self.connection() as db:
            return [
                self._snapshot(row)
                for row in db.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (min(max(limit, 1), 500),),
                )
            ]

    def events(self, job_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT event_json FROM events WHERE job_id=? ORDER BY id", (job_id,)
                )
            ]

    def transition(
        self, job_id: str, expected_revision: int, next_state: str, reason: str | None
    ) -> JobSnapshot:
        with self.transaction() as db:
            job = self._snapshot(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            if job.revision != expected_revision:
                raise StaleRevision("Task changed; reload before updating")
            if (
                next_state == "NEW"
                and db.execute(
                    "SELECT 1 FROM target_attempts a JOIN publication_attempt_links l "
                    "ON l.attempt_id=a.id "
                    "WHERE a.job_id=? AND a.state IN ('SUBMITTING','PROVIDER_ACCEPTED','UNKNOWN')",
                    (job_id,),
                ).fetchone()
            ):
                raise FrozenContent(
                    "Publication outcome requires reconciliation before resuming media"
                )
            if next_state not in _TRANSITIONS[job.state]:
                raise InvalidTransition(f"Cannot transition {job.state} to {next_state}")
            if job.content_frozen and next_state in {"NEW", "CANCELED"}:
                raise FrozenContent("Submitted content requires reconciliation, not requeue/cancel")
            db.execute(
                """UPDATE jobs SET state=?,pause_reason=?,revision=revision+1,updated_at=?,
                         owner_worker_id=CASE WHEN ? IN ('PAUSED','FAILED','CANCELED') THEN NULL
                         ELSE owner_worker_id END WHERE id=?""",
                (next_state, reason, _now(), next_state, job_id),
            )
            self._event(
                db, job_id, {"event": "transition", "status": next_state, "error_code": reason}
            )
        return self.get(job_id)

    def claim_next(self, worker_id: str) -> JobSnapshot | None:
        with self.transaction() as db:
            row = db.execute("""SELECT * FROM jobs WHERE state='NEW' AND owner_worker_id IS NULL
                              AND content_frozen=0 ORDER BY created_at LIMIT 1""").fetchone()
            if row is None:
                return None
            job_id = row["id"]
            db.execute(
                """UPDATE jobs SET state='INSPECTING', owner_worker_id=?,
                         revision=revision+1,updated_at=? WHERE id=?""",
                (worker_id, _now(), job_id),
            )
            self._event(db, job_id, {"event": "claimed", "status": "INSPECTING"})
        return self.get(job_id)

    def revise(self, job_id: str, expected_revision: int, spec: JobSpec) -> JobSnapshot:
        spec = JobSpec.model_validate(spec.model_dump())
        with self.transaction() as db:
            job = self._snapshot(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            if job.content_frozen:
                raise FrozenContent("Shared content is frozen after a possible submission")
            if job.revision != expected_revision:
                raise StaleRevision("Task changed; reload before editing")
            if db.execute(
                "SELECT 1 FROM target_attempts a JOIN publication_attempt_links l "
                "ON l.attempt_id=a.id "
                "WHERE a.job_id=? AND a.state IN ('SUBMITTING','PROVIDER_ACCEPTED','UNKNOWN')",
                (job_id,),
            ).fetchone():
                raise FrozenContent("Reconcile unknown publication before editing content")
            if job.owner_worker_id or job.state in {"INSPECTING", "DOWNLOADING", "PROCESSING"}:
                raise InvalidTransition("Pause active processing before creating a revision")
            revision = job.content_revision + 1
            db.execute(
                """UPDATE jobs SET revision=revision+1,content_revision=?,spec_json=?,
                         state='NEW',source_json=NULL,plan_digest=NULL,output_json=NULL,
                         pause_reason=NULL,owner_worker_id=NULL,updated_at=? WHERE id=?""",
                (revision, spec.model_dump_json(), _now(), job_id),
            )
            db.execute(
                "INSERT INTO job_revisions VALUES (?,?,?,?)",
                (job_id, revision, spec.model_dump_json(), _now()),
            )
            self._event(db, job_id, {"event": "revised", "status": "NEW"})
        return self.get(job_id)

    def record_attempt(self, attempt: TargetAttempt) -> None:
        attempt = TargetAttempt.model_validate(attempt.model_dump())
        with self.transaction() as db:
            job = self._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (attempt.job_id,)).fetchone()
            )
            if attempt.revision != job.content_revision:
                raise StaleRevision("Attempt belongs to another content revision")
            if attempt.target_id not in job.spec.target_ids:
                raise ValueError("Attempt target is outside the task scope")
            now = _now()
            db.execute(
                "INSERT INTO target_attempts VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    attempt.id,
                    attempt.job_id,
                    attempt.revision,
                    attempt.target_id,
                    attempt.payload_digest,
                    attempt.provider_task_id,
                    attempt.state,
                    now,
                    now,
                ),
            )
            if attempt.state in _SIDE_EFFECT_STATES:
                db.execute(
                    """UPDATE jobs SET content_frozen=1,state='PUBLISHING',
                             revision=revision+1,updated_at=? WHERE id=?""",
                    (now, job.id),
                )
            self._event(db, job.id, {"event": "attempt_recorded", "status": attempt.state})

    def attempts(self, job_id: str) -> list[TargetAttempt]:
        with self.connection() as db:
            return [
                TargetAttempt.model_validate({key: row[key] for key in TargetAttempt.model_fields})
                for row in db.execute(
                    "SELECT * FROM target_attempts WHERE job_id=? ORDER BY rowid", (job_id,)
                )
            ]

    def recover_interrupted(self) -> list[str]:
        # Caller must hold the global Worker mutex before invoking this after a restart.
        with self.transaction() as db:
            # Coordinator-owned attempts retain scope/revision in publication_runs. Recover
            # their uncertainty without reviving canceled jobs or changing newer content.
            db.execute(
                "UPDATE target_attempts SET state='UNKNOWN',updated_at=? "
                "WHERE state='SUBMITTING' AND id IN "
                "(SELECT attempt_id FROM publication_attempt_links)",
                (_now(),),
            )
            rows = db.execute("""SELECT DISTINCT jobs.* FROM jobs LEFT JOIN target_attempts a
                 ON jobs.id=a.job_id WHERE jobs.state IN
                 ('INSPECTING','DOWNLOADING','PROCESSING','PUBLISHING')
                 OR a.state='SUBMITTING'""").fetchall()
            result = []
            for row in rows:
                job = self._snapshot(row)
                reason = (
                    "reconcile_required"
                    if job.content_frozen
                    else "interrupted_publication"
                    if job.state == "PUBLISHING"
                    else "interrupted_processing"
                )
                db.execute(
                    """UPDATE target_attempts SET state='UNKNOWN',updated_at=?
                             WHERE job_id=? AND state='SUBMITTING'""",
                    (_now(), job.id),
                )
                db.execute(
                    """UPDATE jobs SET state='PAUSED',pause_reason=?,owner_worker_id=NULL,
                             revision=revision+1,updated_at=? WHERE id=?""",
                    (reason, _now(), job.id),
                )
                self._event(
                    db, job.id, {"event": "recovered", "status": "PAUSED", "error_code": reason}
                )
                result.append(job.id)
            return result
