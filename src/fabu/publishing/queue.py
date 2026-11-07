"""Durable local commands, executed on the existing Windows-owned media worker only."""

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fabu.publishing.fake_gateway import FakePublisher
from fabu.publishing.service import busy, public_run, require_simulation


class PublicationQueue:
    def __init__(
        self,
        store,
        authority,
        coordinator_factory,
        wake,
        readonly_factory=None,
        local_policy=None,
        live_factory=None,
    ):
        self.store, self.authority = store, authority
        self.coordinator_factory, self.wake = coordinator_factory, wake
        self.publisher_factory = lambda authority, cancel: FakePublisher(authority, cancel=cancel)
        self._publishers = {}
        self.readonly_factory = readonly_factory
        self.local_policy, self.live_factory = local_policy, live_factory
        self.tracker = None

    def _validate_action(self, action, scope):
        if action not in {"submit", "reconcile"} or scope not in {"live", "simulation"}:
            raise ValueError("Unknown publication action or scope")
        if action == "submit":
            if scope == "live" and self.local_policy and self.live_factory:
                self.local_policy.check_identity()
            else:
                require_simulation(scope)
        elif scope == "live" and self.readonly_factory is None:
            raise ValueError("Read-only reconciliation is not configured")

    def get_run(self, digest, scope):
        return self.coordinator_factory(None).status(digest, scope)

    def enqueue(self, run, action):
        self._validate_action(action, run["scope"])
        # Capabilities/immutability are checked again by the coordinator at execution time.
        public = public_run(self.store, run, self.local_policy)
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
            self._validate_action(command["action"], command["scope"])
            if command["scope"] != run["scope"]:
                raise ValueError("Command scope does not match the run")
            if cancel.is_set():
                raise ValueError("Canceled before publication")
            coordinator = self.coordinator_factory(worker)
            snapshot = coordinator._snapshot(run)
            permit = None
            if command["scope"] == "live" and command["action"] == "submit":
                permit = self.local_policy.issue(snapshot)
                publisher = self.live_factory(snapshot, permit, cancel)
            elif command["scope"] == "live":
                publisher = self.readonly_factory(cancel)
            else:
                publisher = self._publishers.get(run["id"])
                if publisher is None:
                    publisher = self.publisher_factory(self.authority, cancel)
                    self._publishers[run["id"]] = publisher
                publisher.cancel = cancel
            if command["action"] == "reconcile":
                if (
                    command["scope"] == "live"
                    and self.tracker is not None
                    and self.local_policy is not None
                    and self.local_policy.enabled()
                ):
                    self.tracker.refresh_run(run["id"], cancel)
                else:
                    coordinator.reconcile(snapshot.digest, command["scope"], publisher)
            else:
                permit = permit or self.authority.issue(
                    snapshot,
                    kind="simulation",
                    expires_at=self.authority.clock() + timedelta(hours=1),
                    max_uploads=2,
                    max_submissions_per_target=1,
                    authorization_note="Local authenticated simulation; no network",
                )
                coordinator.run_submission(snapshot.job_id, permit, publisher)
        except Exception as exc:
            from fabu.publishing.local_policy import LocalPolicyError

            state = "FAILED"
            error = str(exc) if isinstance(exc, LocalPolicyError) else "publication_command_failed"
        finally:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE publication_commands SET state=?,error_code=?,updated_at=? WHERE id=?",
                    (state, error, datetime.now(UTC).isoformat(), command["id"]),
                )
            on_state(job_id=None, operation_id=None, kind=None, percent=None)
        return True
