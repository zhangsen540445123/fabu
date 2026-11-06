"""Durable local commands, executed on the existing Windows-owned media worker only."""

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fabu.publishing.fake_gateway import FakePublisher
from fabu.publishing.service import busy, public_run, require_simulation


class PublicationQueue:
    def __init__(self, store, authority, coordinator_factory, wake):
        self.store, self.authority = store, authority
        self.coordinator_factory, self.wake = coordinator_factory, wake
        self.publisher_factory = lambda authority, cancel: FakePublisher(authority, cancel=cancel)
        self._publishers = {}

    def get_run(self, digest, scope):
        return self.coordinator_factory(None).status(digest, scope)

    def enqueue(self, run, action):
        require_simulation(run["scope"])
        if action not in {"submit", "reconcile"}:
            raise ValueError("Unknown publication action")
        # Capabilities/immutability are checked again by the coordinator at execution time.
        public = public_run(self.store, run)
        if not public["can_submit" if action == "submit" else "can_reconcile"]:
            raise ValueError("This operation is not available for the current evidence")
        identifier, now = str(uuid4()), datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            job = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (run["job_id"],)).fetchone()
            )
            current = db.execute(
                "SELECT state FROM publication_runs WHERE id=?", (run["id"],)
            ).fetchone()
            if current["state"] != run["state"] or busy(db, job.id) or job.owner_worker_id:
                raise ValueError("Task is busy or changed")
            if action == "submit" and (
                job.state not in {"READY", "PAUSED"}
                or job.content_revision != run["content_revision"]
            ):
                raise ValueError("Task was canceled or content changed")
            if (
                db.execute(
                    "SELECT COUNT(*) FROM publication_commands WHERE state='QUEUED'"
                ).fetchone()[0]
                >= 64
            ):
                raise ValueError("Publication queue is full")
            try:
                db.execute(
                    "INSERT INTO publication_commands VALUES (?,?,?,?,?,'QUEUED',NULL,?,?)",
                    (identifier, run["id"], job.id, run["scope"], action, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Task already has a publication operation") from exc
        self.wake()
        return {"id": identifier, "state": "QUEUED", "scope": run["scope"]}

    def recover_interrupted(self):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE publication_commands SET state='FAILED', "
                "error_code='publication_interrupted', updated_at=? WHERE state IN ('QUEUED', "
                "'RUNNING')",
                (datetime.now(UTC).isoformat(),),
            )
        self._publishers.clear()

    def process_next(self, worker, cancel, on_state):
        if not worker.active or worker.store.path != self.store.path:
            raise ValueError("Only the existing global worker can process publications")
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM publication_commands WHERE state='QUEUED' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            command = dict(row)
            db.execute(
                "UPDATE publication_commands SET state='RUNNING',updated_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), command["id"]),
            )
            run = dict(
                db.execute(
                    "SELECT * FROM publication_runs WHERE id=?", (command["run_id"],)
                ).fetchone()
            )
        state, error = "COMPLETE", None
        on_state(
            job_id=command["job_id"], operation_id=command["id"], kind="publishing", percent=None
        )
        try:
            require_simulation(command["scope"])
            if cancel.is_set():
                raise ValueError("Canceled before publication")
            coordinator = self.coordinator_factory(worker)
            snapshot = coordinator._snapshot(run)
            publisher = self._publishers.get(run["id"])
            if publisher is None:
                publisher = self.publisher_factory(self.authority, cancel)
                self._publishers[run["id"]] = publisher
            publisher.cancel = cancel
            if command["action"] == "reconcile":
                coordinator.reconcile(snapshot.digest, "simulation", publisher)
            else:
                permit = self.authority.issue(
                    snapshot,
                    kind="simulation",
                    expires_at=self.authority.clock() + timedelta(hours=1),
                    max_uploads=2,
                    max_submissions_per_target=1,
                    authorization_note="Local authenticated simulation; no network",
                )
                coordinator.run_submission(snapshot.job_id, permit, publisher)
        except Exception:
            state, error = "FAILED", "publication_command_failed"
        finally:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE publication_commands SET state=?,error_code=?,updated_at=? WHERE id=?",
                    (state, error, datetime.now(UTC).isoformat(), command["id"]),
                )
            on_state(job_id=None, operation_id=None, kind=None, percent=None)
        return True
