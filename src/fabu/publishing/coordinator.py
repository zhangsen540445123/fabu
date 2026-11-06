"""Transaction-before-side-effect publication coordinator under one global worker owner."""

import sqlite3
from datetime import timedelta
from uuid import uuid4

from fabu.assets import assert_no_reparse, sha256_file
from fabu.domain import TargetAttempt
from fabu.media.models import ENGINE_VERSION
from fabu.media.service import load_job_plan
from fabu.publishing.capabilities import can_enable_mode
from fabu.publishing.gateway import SubmissionReceipt, verify_prepared
from fabu.publishing.journal import SqliteUploadJournal
from fabu.publishing.models import PublicationConfig, PublishSnapshot, verify_publish_snapshot
from fabu.publishing.reconcile import (
    PROVEN,
    persist_receipt,
    reconcile_pending,
    recover_publications,
    release_unused_claims,
)
from fabu.publishing.templates import build_publish_snapshot


class PublicationCoordinator:
    def __init__(self, store, assets, presets, approvals, authority, worker, *, clock=None):
        self.store, self.assets, self.presets, self.approvals = store, assets, presets, approvals
        self.authority, self.worker = authority, worker
        self.clock = clock or authority.clock
        self.checkpoint_hook = None

    def _owner(self):
        if not self.worker.active or self.worker.store.path != self.store.path:
            raise ValueError("Publication requires the global worker owner")

    def _hook(self, point, target=None):
        if self.checkpoint_hook:
            self.checkpoint_hook(point, target)

    def register(self, snapshot, scope):
        verify_publish_snapshot(snapshot)
        if scope not in {"simulation", "live"} or (
            scope == "live" and snapshot.mode == "simulation"
        ):
            raise ValueError("Snapshot execution scope is invalid")
        with self.store.connection() as db:
            row = db.execute(
                "SELECT snapshot_json FROM publication_runs WHERE scope=? AND snapshot_digest=?",
                (scope, snapshot.digest),
            ).fetchone()
        if row:
            if PublishSnapshot.model_validate_json(row[0]) != snapshot:
                raise ValueError("Snapshot identity collision")
            return self.status(snapshot.digest, scope)
        job = self.store.get(snapshot.job_id)
        config = PublicationConfig(
            accounts=tuple(
                {
                    "account": t.account,
                    "declaration": t.declaration,
                    "category": t.category_evidence,
                    "description": t.form["description"],
                }
                for t in snapshot.targets
            ),
            topic_override=snapshot.topic.group_name if snapshot.topic.source == "manual" else None,
        )
        rebuilt = build_publish_snapshot(
            job,
            snapshot.output,
            config,
            store=self.store,
            assets=self.assets,
            presets=self.presets,
            approvals=self.approvals,
            now=snapshot.created_at,
        )
        if rebuilt != snapshot:
            raise ValueError("Snapshot is not the authoritative approved task")
        now = self.clock().isoformat()
        with self.store.transaction() as db:
            current = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            )
            if current != job:
                raise ValueError("Task changed during snapshot registration")
            db.execute(
                "INSERT INTO publication_runs VALUES (?,?,?,?,?,?,'NEW',NULL,NULL,NULL,?,?)",
                (
                    str(uuid4()),
                    job.id,
                    job.content_revision,
                    snapshot.digest,
                    snapshot.model_dump_json(),
                    scope,
                    now,
                    now,
                ),
            )
        return self.status(snapshot.digest, scope)

    def status(self, digest, scope):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM publication_runs WHERE snapshot_digest=? AND scope=?",
                (digest, scope),
            ).fetchone()
        if row is None:
            raise KeyError("Publication snapshot not registered")
        return dict(row)

    def attempts(self, digest, scope):
        run = self.status(digest, scope)
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT a.* FROM target_attempts a JOIN publication_attempt_links l ON "
                "l.attempt_id=a.id "
                "WHERE l.run_id=? ORDER BY a.rowid",
                (run["id"],),
            ).fetchall()
        return [
            TargetAttempt.model_validate({key: row[key] for key in TargetAttempt.model_fields})
            for row in rows
        ]

    def _snapshot(self, run):
        snapshot = PublishSnapshot.model_validate_json(run["snapshot_json"])
        verify_publish_snapshot(snapshot)
        return snapshot

    def _matching_content(self, job, snapshot):
        if (
            job.content_revision != snapshot.content_revision
            or job.spec.title != snapshot.title
            or job.spec.source_url != snapshot.source_url
            or job.spec.publish_mode != snapshot.mode
            or job.spec.scheduled_at_utc != snapshot.scheduled_at_utc
            or tuple(job.spec.target_ids) != tuple(t.account.account_id for t in snapshot.targets)
            or job.plan_digest != snapshot.output.plan_digest
            or job.output_manifest != snapshot.output.model_dump(mode="json")
        ):
            raise ValueError("Task content differs from frozen publication snapshot")

    def _files_and_approval(self, snapshot):
        job = self.store.get(snapshot.job_id)
        self._matching_content(job, snapshot)
        plan = load_job_plan(self.store, job.id)
        preset = self.presets.get_version(job.spec.preset_version_id)
        if plan.engine_version != ENGINE_VERSION or not self.approvals.approval_matches(
            preset, plan.asset_refs, ENGINE_VERSION
        ):
            raise ValueError("Publication preset approval is no longer valid")
        for item in [snapshot.output, snapshot.cover]:
            path = item.path if hasattr(item, "path") else item.local_path
            assert_no_reparse(path)
            if (
                not path.is_file()
                or path.stat().st_size != item.size_bytes
                or sha256_file(path) != item.sha256
            ):
                raise ValueError("Publication resource is missing or changed")

    def _check_claims(self, db, run, snapshot):
        for target in snapshot.targets:
            row = db.execute(
                "SELECT run_id FROM publication_source_claims WHERE scope=? AND video_id=? "
                "AND target_id=?",
                (run["scope"], snapshot.video_id, target.account.account_id),
            ).fetchone()
            if row and row[0] != run["id"]:
                raise ValueError(
                    "This source/account already has another pending or submitted publication"
                )

    def _assert_resumable(self, run, snapshot):
        with self.store.connection() as db:
            self._check_claims(db, run, snapshot)
            rows = db.execute(
                "SELECT a.state,l.definitely_not_submitted FROM target_attempts a "
                "JOIN publication_attempt_links l ON l.attempt_id=a.id WHERE l.run_id=?",
                (run["id"],),
            ).fetchall()
        if any(
            row["state"] in {"SUBMITTING", "UNKNOWN", "PROVIDER_ACCEPTED"}
            or row["state"] == "FAILED"
            and not row["definitely_not_submitted"]
            for row in rows
        ):
            raise ValueError("Pending or unknown submission requires reconcile before resume")

    def _activate(self, run, snapshot):
        now = self.clock().isoformat()
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT * FROM publication_runs WHERE id=?", (run["id"],)
            ).fetchone()
            job = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (snapshot.job_id,)).fetchone()
            )
            if (
                current["state"] not in {"NEW", "PAUSED"}
                or job.state not in {"READY", "PAUSED"}
                or job.owner_worker_id
            ):
                raise ValueError("Task is not available for publication")
            self._matching_content(job, snapshot)
            self._check_claims(db, run, snapshot)
            try:
                db.execute(
                    "UPDATE publication_runs SET "
                    "state='ACTIVE',reason=NULL,stage='PREFLIGHT', "
                    "owner_worker_id=?,updated_at=? WHERE id=?",
                    (self.worker.id, now, run["id"]),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Another publication is active") from exc
            db.execute(
                "UPDATE jobs SET "
                "state='PUBLISHING',owner_worker_id=?,revision=revision+1,updated_at=? WHERE id=?",
                (self.worker.id, now, job.id),
            )

    def _reserve_sources(self, run, snapshot):
        with self.store.transaction() as db:
            self._check_claims(db, run, snapshot)
            for target in snapshot.targets:
                db.execute(
                    "INSERT OR IGNORE INTO publication_source_claims VALUES (?,?,?,?)",
                    (run["scope"], snapshot.video_id, target.account.account_id, run["id"]),
                )
            db.execute("UPDATE publication_runs SET stage='PREPARING' WHERE id=?", (run["id"],))

    def _finish(self, run, state, reason=None):
        now = self.clock().isoformat()
        job_state = (
            "COMPLETED"
            if state == "COMPLETE" and run["scope"] == "live"
            else "READY"
            if state == "COMPLETE"
            else "PAUSED"
        )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE publication_runs SET "
                "state=?,reason=?,owner_worker_id=NULL,updated_at=? WHERE id=?",
                (state, reason, now, run["id"]),
            )
            db.execute(
                "UPDATE jobs SET "
                "state=?,pause_reason=?,owner_worker_id=NULL,revision=revision+1, "
                "updated_at=? WHERE id=? AND content_revision=? AND state='PUBLISHING' "
                "AND owner_worker_id=?",
                (job_state, reason, now, run["job_id"], run["content_revision"], self.worker.id),
            )
            release_unused_claims(db, run)
            self.store._event(
                db,
                run["job_id"],
                {"event": "publication_run", "status": state, "error_code": reason},
            )
        return self.store.get(run["job_id"])

    def _begin_attempt(self, run, snapshot, target, ordinal):
        self._hook("before_attempt", target.account.account_id)
        now = self.clock().isoformat()
        with self.store.transaction() as db:
            owner = db.execute(
                "SELECT state,owner_worker_id FROM publication_runs WHERE id=?", (run["id"],)
            ).fetchone()
            if owner["state"] != "ACTIVE" or owner["owner_worker_id"] != self.worker.id:
                raise ValueError("Publication run lost ownership")
            current_job = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (snapshot.job_id,)).fetchone()
            )
            self._matching_content(current_job, snapshot)
            if current_job.state != "PUBLISHING" or current_job.owner_worker_id != self.worker.id:
                raise ValueError("Task canceled or publication ownership lost")
            rows = db.execute(
                "SELECT a.state,l.definitely_not_submitted FROM target_attempts a "
                "JOIN publication_attempt_links l ON l.attempt_id=a.id WHERE l.run_id=? "
                "AND a.target_id=? ORDER BY a.rowid",
                (run["id"], target.account.account_id),
            ).fetchall()
            if rows and (rows[-1]["state"] != "FAILED" or not rows[-1]["definitely_not_submitted"]):
                raise ValueError(
                    "Target cannot be submitted again without definite non-submission evidence"
                )
            if len(rows) >= 3:
                raise ValueError("Target attempt limit reached")
            attempt = TargetAttempt(
                job_id=snapshot.job_id,
                revision=snapshot.content_revision,
                target_id=target.account.account_id,
                payload_digest=snapshot.digest,
                state="SUBMITTING",
            )
            db.execute(
                "INSERT INTO target_attempts VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    attempt.id,
                    attempt.job_id,
                    attempt.revision,
                    attempt.target_id,
                    attempt.payload_digest,
                    None,
                    attempt.state,
                    now,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO publication_attempt_links VALUES (?,?,?,NULL,0)",
                (attempt.id, run["id"], ordinal),
            )
            db.execute(
                "UPDATE jobs SET content_frozen=CASE WHEN ?='live' THEN 1 ELSE "
                "content_frozen END,revision=revision+1,updated_at=? WHERE id=?",
                (run["scope"], now, snapshot.job_id),
            )
            db.execute(
                "UPDATE publication_runs SET stage='SUBMITTING',updated_at=? WHERE id=?",
                (now, run["id"]),
            )
        self._hook("after_attempt", target.account.account_id)
        return attempt

    def _preflight_problem(self, snapshot, report):
        now = self.clock()
        if report.checked_at.utcoffset() is None or not -timedelta(
            seconds=5
        ) <= now - report.checked_at <= timedelta(minutes=5):
            return "preflight_expired"
        mode = "native_schedule" if snapshot.mode == "native_schedule" else "immediate"
        if not can_enable_mode(report, mode, [t.account.account_id for t in snapshot.targets]):
            return "capabilities_unverified"
        if report.submission_strategy != "per_target":
            return "submission_strategy_unverified"
        if snapshot.scheduled_at_utc is not None:
            for target in snapshot.targets:
                window = report.schedule_windows.get(target.account.account_id)
                if window is None:
                    return "schedule_window_unverified"
                if (
                    not max(now, window.earliest_utc)
                    < snapshot.scheduled_at_utc
                    <= window.latest_utc
                ):
                    return "schedule_outside_platform_window"
        return None

    def run_submission(self, job_id, permit, publisher):
        self._owner()
        execution = "simulation" if permit.kind == "simulation" else "live"
        if getattr(publisher, "execution_scope", None) != execution:
            raise ValueError("Publisher/permit execution scope mismatch")
        run = self.status(permit.snapshot_digest, execution)
        if run["job_id"] != job_id:
            raise ValueError("Permit does not belong to this job")
        snapshot = self._snapshot(run)
        self.authority.validate(permit, snapshot, real=execution == "live")
        if run["state"] == "COMPLETE":
            return self.store.get(job_id)
        self._assert_resumable(run, snapshot)
        self._files_and_approval(snapshot)
        self._activate(run, snapshot)
        try:
            report = publisher.preflight(snapshot)
            issue = self._preflight_problem(snapshot, report)
            if issue:
                return self._finish(run, "PAUSED", issue)
            self.authority.validate(permit, snapshot, real=execution == "live")
            self._reserve_sources(run, snapshot)
            publisher.journal = SqliteUploadJournal(self.store, snapshot, scope=execution)
            prepared = publisher.prepare(snapshot, permit)
            verify_prepared(prepared, self.authority)
            if prepared.snapshot != snapshot:
                raise ValueError("Prepared payload belongs to another snapshot")
            self._hook("after_prepare")
            for ordinal, target in enumerate(snapshot.targets):
                previous = [
                    a
                    for a in self.attempts(snapshot.digest, execution)
                    if a.target_id == target.account.account_id
                ]
                if previous and previous[-1].state in PROVEN:
                    continue
                self.authority.validate(permit, snapshot, real=execution == "live")
                issue = self._preflight_problem(snapshot, report)
                if issue == "preflight_expired":
                    report = publisher.preflight(snapshot)
                    issue = self._preflight_problem(snapshot, report)
                if issue:
                    return self._finish(run, "PAUSED", issue)
                self._files_and_approval(snapshot)
                attempt = self._begin_attempt(run, snapshot, target, ordinal)
                try:
                    receipt = publisher.submit(prepared, permit, target.account.account_id)
                except Exception:
                    receipt = SubmissionReceipt(
                        target_id=target.account.account_id,
                        state="UNKNOWN",
                        reason="submission_outcome_unknown",
                    )
                self._hook("after_submit", target.account.account_id)
                checked = persist_receipt(self.store, attempt, snapshot, execution, receipt)
                if checked.state == "PROVIDER_ACCEPTED":
                    results = reconcile_pending(
                        self.store, publisher, worker=self.worker, run_id=run["id"]
                    )
                    checked = next(
                        (r for r in results if r.target_id == target.account.account_id), checked
                    )
                if checked.state not in PROVEN:
                    return self._finish(
                        run,
                        "PAUSED",
                        "target_failed" if checked.state == "FAILED" else "reconcile_required",
                    )
            return self._finish(run, "COMPLETE")
        except Exception:
            return self._finish(run, "PAUSED", "publication_stage_failed")

    def recover_interrupted(self):
        self._owner()
        SqliteUploadJournal.recover_interrupted(self.store)
        return recover_publications(self.store)

    def reconcile(self, digest, scope, publisher):
        self._owner()
        run = self.status(digest, scope)
        return reconcile_pending(self.store, publisher, worker=self.worker, run_id=run["id"])
