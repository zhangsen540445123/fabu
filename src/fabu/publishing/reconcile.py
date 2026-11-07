"""Promote only uniquely bound platform evidence; never infer publication from time or title."""

from datetime import UTC, datetime

from fabu.domain import TargetAttempt
from fabu.publishing.gateway import ReconcileResult, SubmissionReceipt
from fabu.publishing.models import PublishSnapshot, verify_publish_snapshot

PROVEN = {"SUBMITTED", "SCHEDULED", "PUBLISHED"}


def checked_receipt(snapshot, scope, target_id, receipt, provider_task_id=None):
    target = next((t for t in snapshot.targets if t.account.account_id == target_id), None)
    expected_origin = "simulation" if scope == "simulation" else "verified_platform"

    def unknown(reason):
        return SubmissionReceipt(
            target_id=target_id,
            state="UNKNOWN",
            provider_task_id=provider_task_id or receipt.provider_task_id,
            reason=reason,
        )

    if target is None or receipt.target_id != target_id:
        return unknown("evidence_account_mismatch")
    if provider_task_id and receipt.provider_task_id != provider_task_id:
        return unknown("evidence_provider_task_mismatch")
    if receipt.state in PROVEN or receipt.state == "FAILED":
        if (
            receipt.evidence_origin != expected_origin
            or receipt.snapshot_digest != snapshot.digest
            or receipt.video_sha256 != snapshot.output.sha256
            or receipt.cover_sha256 != snapshot.cover.sha256
            or receipt.platform != target.account.platform
            or not receipt.provider_task_id
        ):
            return unknown("evidence_not_uniquely_bound")
        if receipt.state in PROVEN:
            if not receipt.platform_content_id:
                return unknown("missing_platform_content_id")
            if snapshot.mode == "native_schedule" and (
                receipt.state not in {"SCHEDULED", "PUBLISHED"}
                or receipt.scheduled_at_utc != snapshot.scheduled_at_utc
            ):
                return unknown("platform_schedule_not_proven")
            if snapshot.mode != "native_schedule" and receipt.state == "SCHEDULED":
                return unknown("unexpected_platform_schedule")
    elif receipt.state == "PROVIDER_ACCEPTED" and not receipt.provider_task_id:
        return unknown("missing_provider_task_id")
    return SubmissionReceipt.model_validate(receipt.model_dump())


def persist_receipt(store, attempt, snapshot, scope, receipt, *, from_reconcile=False):
    checked = checked_receipt(
        snapshot,
        scope,
        attempt.target_id,
        receipt,
        attempt.provider_task_id if from_reconcile else None,
    )
    now = datetime.now(UTC).isoformat()
    with store.transaction() as db:
        current = db.execute(
            "SELECT a.state,l.receipt_json FROM target_attempts a "
            "JOIN publication_attempt_links l ON a.id=l.attempt_id WHERE a.id=?",
            (attempt.id,),
        ).fetchone()
        if current is None:
            raise ValueError("Attempt disappeared")
        if current["state"] in PROVEN:
            if checked.state == "FAILED" and current["receipt_json"]:
                previous = SubmissionReceipt.model_validate_json(current["receipt_json"])
                if (
                    not checked.platform_content_id
                    or checked.platform_content_id != previous.platform_content_id
                ):
                    return checked
                # A platform rejection after accepted/scheduled content does not prove that no
                # submission occurred. Record the failure, but never authorize another submit.
                checked = checked.model_copy(update={"definitely_not_submitted": False})
            elif checked.state not in PROVEN:
                return checked  # keep proven content on inconclusive later query
            elif {"SUBMITTED": 1, "SCHEDULED": 2, "PUBLISHED": 3}[checked.state] < {
                "SUBMITTED": 1,
                "SCHEDULED": 2,
                "PUBLISHED": 3,
            }[current["state"]]:
                return checked
        db.execute(
            "UPDATE target_attempts SET state=?,provider_task_id=?,updated_at=? WHERE id=?",
            (checked.state, checked.provider_task_id, now, attempt.id),
        )
        db.execute(
            "UPDATE publication_attempt_links SET "
            "receipt_json=?,definitely_not_submitted=? WHERE attempt_id=?",
            (checked.model_dump_json(), int(checked.definitely_not_submitted), attempt.id),
        )
        if from_reconcile:
            db.execute(
                "INSERT INTO publication_reconcile_log(attempt_id,result_json,checked_at) "
                "VALUES (?,?,?)",
                (attempt.id, checked.model_dump_json(), now),
            )
        store._event(
            db,
            attempt.job_id,
            {
                "event": "publication_evidence",
                "target_id": attempt.target_id,
                "status": checked.state,
            },
        )
    return checked


def reconcile_pending(store, publisher, *, worker, run_id=None):
    if not worker.active or worker.store.path != store.path:
        raise ValueError("Reconciliation requires the global worker owner")
    with store.connection() as db:
        rows = db.execute(
            "SELECT a.*,r.snapshot_json,r.scope FROM target_attempts a "
            "JOIN publication_attempt_links l ON l.attempt_id=a.id JOIN publication_runs r "
            "ON r.id=l.run_id "
            "WHERE a.state IN ('SUBMITTING','PROVIDER_ACCEPTED','UNKNOWN','SUBMITTED','SCHEDULED') "
            "AND (? IS NULL "
            "OR r.id=?)",
            (run_id, run_id),
        ).fetchall()
    results = []
    for row in rows:
        if row["scope"] != getattr(publisher, "execution_scope", None):
            continue
        snapshot = PublishSnapshot.model_validate_json(row["snapshot_json"])
        verify_publish_snapshot(snapshot)
        attempt = TargetAttempt.model_validate(
            {key: row[key] for key in TargetAttempt.model_fields}
        )
        if not attempt.provider_task_id:
            result = ReconcileResult(
                target_id=attempt.target_id, state="UNKNOWN", reason="missing_provider_identity"
            )
            checked = persist_receipt(
                store, attempt, snapshot, row["scope"], result, from_reconcile=True
            )
            results.append(
                ReconcileResult.model_validate(checked.model_dump(exclude={"actual_channel"}))
            )
            continue
        try:
            result = publisher.reconcile(attempt)
        except Exception:
            result = ReconcileResult(
                target_id=attempt.target_id,
                state="UNKNOWN",
                provider_task_id=attempt.provider_task_id,
                reason="query_failed",
            )
        checked = persist_receipt(
            store, attempt, snapshot, row["scope"], result, from_reconcile=True
        )
        results.append(
            ReconcileResult.model_validate(checked.model_dump(exclude={"actual_channel"}))
        )
    return results


def release_unused_claims(db, run):
    """Release only a run with no submission and no uncertain external upload."""
    db.execute(
        "DELETE FROM publication_source_claims WHERE run_id=? "
        "AND NOT EXISTS (SELECT 1 FROM publication_attempt_links WHERE run_id=?) "
        "AND NOT EXISTS (SELECT 1 FROM upload_attempts u WHERE "
        "u.scope=publication_source_claims.scope "
        "AND u.source_video_id=publication_source_claims.video_id "
        "AND u.state IN ('SUBMITTING','UNKNOWN'))",
        (run["id"], run["id"]),
    )


def recover_publications(store):
    # Called only after taking global worker ownership at startup.
    now = datetime.now(UTC).isoformat()
    with store.transaction() as db:
        rows = db.execute("SELECT * FROM publication_runs WHERE state='ACTIVE'").fetchall()
        for row in rows:
            db.execute(
                "UPDATE target_attempts SET state='UNKNOWN',updated_at=? WHERE state='SUBMITTING' "
                "AND id IN (SELECT attempt_id FROM publication_attempt_links WHERE run_id=?)",
                (now, row["id"]),
            )
            db.execute(
                "UPDATE publication_runs SET "
                "state='PAUSED',reason='interrupted_publication', "
                "owner_worker_id=NULL,updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            db.execute(
                "UPDATE jobs SET "
                "state='PAUSED',pause_reason='interrupted_publication',owner_worker_id=NULL, "
                "revision=revision+1,updated_at=? WHERE id=? AND content_revision=? "
                "AND state='PUBLISHING' AND owner_worker_id=?",
                (now, row["job_id"], row["content_revision"], row["owner_worker_id"]),
            )
            release_unused_claims(db, row)
    return [row["id"] for row in rows]
