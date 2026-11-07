from datetime import UTC, datetime, timedelta

import pytest
from test_templates import build


@pytest.fixture
def coordination(publishing_context):
    from fabu.publishing.coordinator import PublicationCoordinator
    from fabu.publishing.permits import PermitAuthority
    from fabu.worker import Worker

    store, assets, presets, approvals, *_ = publishing_context
    authority = PermitAuthority()
    with Worker(store) as worker:
        coordinator = PublicationCoordinator(store, assets, presets, approvals, authority, worker)
        yield coordinator, authority


def scope(authority, snapshot):
    return authority.issue(
        snapshot,
        kind="simulation",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_uploads=2,
        max_submissions_per_target=1,
        authorization_note="Local test only",
    )


def controlled_verification(store, authority, snapshot, permit):
    from test_controlled_verification import safety

    from fabu.publishing.verification import VerificationService

    service = VerificationService(store, authority)
    context = (service, authority, snapshot, permit)
    evidence = service.record(permit, snapshot, safety(context))
    return {
        "verification": service,
        "controlled_gate": service.controlled_gate(permit, snapshot, evidence),
    }


def test_failure_stops_remaining_targets(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority, outcomes=["SUBMITTED", "FAILED", "SUBMITTED"])
    result = coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    attempts = coordinator.attempts(snapshot.digest, "simulation")
    assert [a.state for a in attempts] == ["SUBMITTED", "FAILED"]
    assert [c[1] for c in publisher.calls if c[0] == "submit"] == list(
        t.account.account_id for t in snapshot.targets[:2]
    )
    assert result.state == "PAUSED"
    assert coordinator.status(snapshot.digest, "simulation")["state"] == "PAUSED"
    assert not result.content_frozen  # simulation cannot permanently freeze real user content


def test_unknown_does_not_retry_after_restart(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority, outcomes=["UNKNOWN"])
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    coordinator.recover_interrupted()
    before = list(publisher.calls)
    with pytest.raises(ValueError, match="reconcile"):
        coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert publisher.calls == before
    assert len(coordinator.attempts(snapshot.digest, "simulation")) == 1


def test_title_or_preset_change_cannot_bypass_unknown(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.models import publish_snapshot_digest

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority, outcomes=["UNKNOWN"])
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    store = publishing_context[0]
    old = store.get(snapshot.job_id)
    other = store.create(old.spec.model_copy(update={"title": "剪发 新标题"}))
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET state='READY',source_json=?,plan_digest=?,output_json=? WHERE id=?",
            (
                old.source_manifest and __import__("json").dumps(old.source_manifest),
                old.plan_digest,
                __import__("json").dumps(old.output_manifest),
                other.id,
            ),
        )
        row = db.execute("SELECT * FROM processing_snapshots WHERE job_id=?", (old.id,)).fetchone()
        db.execute(
            "INSERT INTO processing_snapshots VALUES (?,?,?,?,?,?)",
            (
                other.id,
                1,
                row["preset_version_id"],
                row["source_json"],
                row["plan_json"],
                row["created_at"],
            ),
        )
    new = store.get(other.id)
    changed = snapshot.model_copy(
        update={
            "job_id": new.id,
            "prepared_revision": new.revision,
            "title": new.spec.title,
            "targets": tuple(
                t.model_copy(update={"form": {**t.form, "title": new.spec.title}})
                for t in snapshot.targets
            ),
        }
    )
    changed = changed.model_copy(update={"digest": publish_snapshot_digest(changed)})
    coordinator.register(changed, "simulation")
    before = list(publisher.calls)
    with pytest.raises(ValueError, match="source"):
        coordinator.run_submission(new.id, scope(authority, changed), publisher)
    assert publisher.calls == before


def test_expired_schedule_never_becomes_immediate(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.models import publish_snapshot_digest

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    when = (datetime.now(UTC) + timedelta(seconds=30)).replace(microsecond=0)
    snapshot = snapshot.model_copy(
        update={
            "mode": "native_schedule",
            "scheduled_at_utc": when,
            "targets": tuple(
                t.model_copy(
                    update={"form": {**t.form, "scheduledTime": int(when.timestamp() * 1000)}}
                )
                for t in snapshot.targets
            ),
        }
    )
    # Reflect requested mode in authoritative task for registration.
    store = publishing_context[0]
    job = store.get(snapshot.job_id)
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            (
                job.spec.model_copy(
                    update={"publish_mode": "native_schedule", "scheduled_at_utc": when}
                ).model_dump_json(),
                job.id,
            ),
        )
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    coordinator.register(snapshot, "simulation")
    permit = scope(authority, snapshot)
    authority.clock = lambda: when + timedelta(seconds=1)
    publisher = FakePublisher(authority)
    with pytest.raises(ValueError):
        coordinator.run_submission(snapshot.job_id, permit, publisher)
    assert not publisher.calls


@pytest.mark.parametrize("crash_point", ["before_attempt", "after_attempt", "after_submit"])
def test_crash_points_do_not_blindly_reissue(publishing_context, coordination, crash_point):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)

    class Crash(BaseException):
        pass

    def hook(point, target):
        if point == crash_point:
            raise Crash()

    coordinator.checkpoint_hook = hook
    with pytest.raises(Crash):
        coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    coordinator.checkpoint_hook = None
    coordinator.recover_interrupted()
    attempted = coordinator.attempts(snapshot.digest, "simulation")
    if crash_point == "before_attempt":
        assert not attempted
        coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
        assert len([c for c in publisher.calls if c[0] == "submit"]) == 3
    else:
        assert attempted[0].state == "UNKNOWN"
        before = list(publisher.calls)
        with pytest.raises(ValueError, match="reconcile"):
            coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
        assert publisher.calls == before


def test_success_complete_does_not_republish(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    result = coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert result.state == "READY"
    assert coordinator.status(snapshot.digest, "simulation")["state"] == "COMPLETE"
    before = list(publisher.calls)
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert publisher.calls == before


def test_unverified_preflight_does_not_upload(publishing_context, coordination):
    from fabu.publishing.capabilities import CapabilityReport
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    publisher.preflight = lambda snap: CapabilityReport(checked_at=datetime.now(UTC))
    result = coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert result.state == "PAUSED" and publisher.calls == []
    assert coordinator.status(snapshot.digest, "simulation")["reason"] == "capabilities_unverified"


def test_reconcile_requires_known_provider_identity_after_lost_response(
    publishing_context, coordination
):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)

    class Crash(BaseException):
        pass

    coordinator.checkpoint_hook = lambda point, target: (
        (_ for _ in ()).throw(Crash()) if point == "after_submit" else None
    )
    with pytest.raises(Crash):
        coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    coordinator.recover_interrupted()
    result = coordinator.reconcile(snapshot.digest, "simulation", publisher)
    assert result[0].state == "UNKNOWN"
    assert not [c for c in publisher.calls if c[0] == "reconcile"]


def test_content_change_between_check_and_attempt_never_submits(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    store = publishing_context[0]

    def hook(point, target):
        if point == "before_attempt":
            job = store.get(snapshot.job_id)
            with store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET spec_json=? WHERE id=?",
                    (
                        job.spec.model_copy(update={"title": "changed title"}).model_dump_json(),
                        job.id,
                    ),
                )

    coordinator.checkpoint_hook = hook
    result = coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert result.state == "PAUSED"
    assert not coordinator.attempts(snapshot.digest, "simulation")
    assert not [c for c in publisher.calls if c[0] == "submit"]


def test_prepared_snapshot_cannot_be_swapped_by_provider(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.gateway import assemble_prepared
    from fabu.publishing.models import publish_snapshot_digest

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    original = publisher.prepare

    def changed(snap, permit):
        prepared = original(snap, permit)
        other = snap.model_copy(
            update={
                "title": "different",
                "targets": tuple(
                    t.model_copy(update={"form": {**t.form, "title": "different"}})
                    for t in snap.targets
                ),
            }
        )
        other = other.model_copy(update={"digest": publish_snapshot_digest(other)})
        return assemble_prepared(other, prepared.resources, prepared.client_id, authority)

    publisher.prepare = changed
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert not coordinator.attempts(snapshot.digest, "simulation")


def test_resume_known_failure_keeps_completed_targets_and_reuses_uploads(
    publishing_context, coordination
):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority, outcomes=["SUBMITTED", "FAILED", "SUBMITTED", "SUBMITTED"])
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    fresh = authority.issue(
        snapshot,
        kind="simulation",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_uploads=0,
        max_submissions_per_target=1,
        authorization_note="Retry known non-submission only",
    )
    coordinator.run_submission(snapshot.job_id, fresh, publisher)
    assert [c[1] for c in publisher.calls if c[0] == "submit"] == [
        snapshot.targets[i].account.account_id for i in [0, 1, 1, 2]
    ]
    assert coordinator.status(snapshot.digest, "simulation")["state"] == "COMPLETE"
    with publishing_context[0].connection() as db:
        assert db.execute("SELECT COUNT(*) FROM upload_attempts").fetchone()[0] == 2


def test_batch_receipt_does_not_promise_rollback(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority, outcomes=["SUBMITTED", "FAILED"])
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    attempts = coordinator.attempts(snapshot.digest, "simulation")
    assert attempts[0].state == "SUBMITTED"  # a later failure cannot revoke what was already sent
    assert not any(
        c[0] in {"delete", "rollback", "cancel_remote", "submit_batch"} for c in publisher.calls
    )


def test_wrong_evidence_binding_or_schedule_stays_unknown(publishing_context):
    from fabu.publishing.gateway import SubmissionReceipt
    from fabu.publishing.reconcile import checked_receipt

    snapshot = build(publishing_context)
    target = snapshot.targets[0]
    receipt = SubmissionReceipt(
        target_id=target.account.account_id,
        state="PUBLISHED",
        provider_task_id="provider-1",
        platform_content_id="platform-1",
        snapshot_digest=snapshot.digest,
        video_sha256=snapshot.output.sha256,
        cover_sha256=snapshot.cover.sha256,
        platform=target.account.platform,
        evidence_origin="simulation",
    )
    for changes in [
        {"target_id": "wrong"},
        {"snapshot_digest": "f" * 64},
        {"video_sha256": "f" * 64},
        {"evidence_origin": "verified_platform"},
        {"platform_content_id": None},
    ]:
        assert (
            checked_receipt(
                snapshot,
                "simulation",
                target.account.account_id,
                receipt.model_copy(update=changes),
            ).state
            == "UNKNOWN"
        )
    assert (
        checked_receipt(
            snapshot,
            "simulation",
            target.account.account_id,
            receipt,
            provider_task_id="other-task",
        ).state
        == "UNKNOWN"
    )


def test_unknown_submission_strategy_blocks_before_upload(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    original = publisher.preflight
    publisher.preflight = lambda snap: original(snap).model_copy(
        update={"submission_strategy": "UNKNOWN"}
    )
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert not publisher.calls
    assert (
        coordinator.status(snapshot.digest, "simulation")["reason"]
        == "submission_strategy_unverified"
    )


def test_paid_preflight_gate_and_active_owner_block(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    original = publisher.preflight
    publisher.preflight = lambda snap: original(snap).model_copy(update={"payment_required": True})
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert not publisher.calls
    coordinator.worker.active = False
    try:
        with pytest.raises(ValueError, match="worker"):
            coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    finally:
        coordinator.worker.active = True


def test_platform_native_schedule_limits_and_stale_preflight(publishing_context, coordination):
    from fabu.publishing.capabilities import ScheduleWindow
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.models import publish_snapshot_digest

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    when = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
    job = publishing_context[0].get(snapshot.job_id)
    with publishing_context[0].transaction() as db:
        db.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            (
                job.spec.model_copy(
                    update={"publish_mode": "native_schedule", "scheduled_at_utc": when}
                ).model_dump_json(),
                job.id,
            ),
        )
    snapshot = snapshot.model_copy(
        update={
            "mode": "native_schedule",
            "scheduled_at_utc": when,
            "targets": tuple(
                t.model_copy(
                    update={"form": {**t.form, "scheduledTime": int(when.timestamp() * 1000)}}
                )
                for t in snapshot.targets
            ),
        }
    )
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    old = publisher.preflight
    publisher.preflight = lambda snap: old(snap).model_copy(
        update={
            "schedule_windows": {
                t.account.account_id: ScheduleWindow(
                    earliest_utc=when + timedelta(minutes=1), latest_utc=when + timedelta(days=1)
                )
                for t in snap.targets
            }
        }
    )
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert not publisher.calls
    assert (
        coordinator.status(snapshot.digest, "simulation")["reason"]
        == "schedule_outside_platform_window"
    )


def test_concurrent_submission_only_one_run_owns_side_effects(publishing_context, coordination):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    entered = Event()
    release = Event()
    original = publisher.preflight

    def wait(snap):
        entered.set()
        release.wait(5)
        return original(snap)

    publisher.preflight = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            coordinator.run_submission, snapshot.job_id, scope(authority, snapshot), publisher
        )
        assert entered.wait(3)
        try:
            with pytest.raises(ValueError):
                coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
        finally:
            release.set()
        assert first.result(timeout=10).state == "READY"
    assert len([c for c in publisher.calls if c[0] == "submit"]) == 3


def test_cancel_before_submission_stays_canceled_and_releases_unused_claims(
    publishing_context, coordination
):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)
    store = publishing_context[0]
    original = publisher.prepare

    def prepare(snap, permit):
        result = original(snap, permit)
        with store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='CANCELED',owner_worker_id=NULL WHERE id=?", (snap.job_id,)
            )
        return result

    publisher.prepare = prepare
    result = coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert result.state == "CANCELED"
    assert not [c for c in publisher.calls if c[0] == "submit"]
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM publication_source_claims").fetchone()[0] == 0


def test_reconcile_can_resume_only_after_unique_bound_platform_proof(
    publishing_context, coordination
):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority, outcomes=["UNKNOWN", "SUBMITTED", "SUBMITTED"])
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    first = snapshot.targets[0].account.account_id
    receipt = publisher.receipts[first]
    publisher.receipts[first] = receipt.model_copy(
        update={"state": "SUBMITTED", "platform_content_id": "platform-confirmed-id"}
    )
    result = coordinator.reconcile(snapshot.digest, "simulation", publisher)
    assert result[0].state == "SUBMITTED"
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert [c[1] for c in publisher.calls if c[0] == "submit"] == [
        t.account.account_id for t in snapshot.targets
    ]
    assert coordinator.status(snapshot.digest, "simulation")["state"] == "COMPLETE"


def test_live_first_attempt_freezes_shared_content_before_transport(
    publishing_context, coordination
):
    # A hand-written local fake with no network/executable path tests live-state semantics only.
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.gateway import (
        SubmissionReceipt,
        UploadReceipt,
        assemble_prepared,
        resource_metadata,
    )
    from fabu.publishing.models import publish_snapshot_digest

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    store = publishing_context[0]
    job = store.get(snapshot.job_id)
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            (job.spec.model_copy(update={"publish_mode": "immediate"}).model_dump_json(), job.id),
        )
    snapshot = snapshot.model_copy(update={"mode": "immediate"})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    coordinator.register(snapshot, "live")

    class LocalFake:
        execution_scope = "live"
        calls = []

        def preflight(self, snap):
            return (
                FakePublisher(authority)
                .preflight(snap)
                .model_copy(update={"tool_version": "3.2.32"})
            )

        def prepare(self, snap, permit):
            values = tuple(
                UploadReceipt(
                    resource=k,
                    sha256=d,
                    key="cloud-publish/test/" + k,
                    metadata=resource_metadata(snap, k),
                    raw={},
                )
                for k, d in [("video", snap.output.sha256), ("cover", snap.cover.sha256)]
            )
            return assemble_prepared(snap, values, "test-client", authority)

        def submit(self, prepared, permit, target):
            assert store.get(snapshot.job_id).content_frozen
            assert coordinator.attempts(snapshot.digest, "live")[-1].state == "SUBMITTING"
            self.calls.append(target)
            return SubmissionReceipt(target_id=target, state="UNKNOWN", reason="synthetic_unknown")

    publisher = LocalFake()
    permit = authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=datetime.now(UTC) + timedelta(minutes=3),
        max_uploads=0,
        max_submissions_per_target=1,
        authorization_note="Synthetic DB-only live semantics",
    )
    coordinator.run_submission(
        snapshot.job_id,
        permit,
        publisher,
        **controlled_verification(store, authority, snapshot, permit),
    )
    frozen = store.get(snapshot.job_id)
    assert frozen.content_frozen and frozen.state == "PAUSED" and len(publisher.calls) == 1
    with pytest.raises(ValueError):
        store.revise(frozen.id, frozen.revision, frozen.spec)


def test_live_complete_is_a_distinct_terminal_job_state(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.gateway import (
        SubmissionReceipt,
        UploadReceipt,
        assemble_prepared,
        resource_metadata,
    )
    from fabu.publishing.models import publish_snapshot_digest

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    store = publishing_context[0]
    job = store.get(snapshot.job_id)
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            (job.spec.model_copy(update={"publish_mode": "immediate"}).model_dump_json(), job.id),
        )
    snapshot = snapshot.model_copy(update={"mode": "immediate"})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    coordinator.register(snapshot, "live")

    class LocalFake:
        execution_scope = "live"

        def preflight(self, snap):
            return (
                FakePublisher(authority)
                .preflight(snap)
                .model_copy(update={"tool_version": "3.2.32"})
            )

        def prepare(self, snap, permit):
            values = tuple(
                UploadReceipt(
                    resource=k,
                    sha256=d,
                    key="cloud-publish/test/" + k,
                    metadata=resource_metadata(snap, k),
                    raw={},
                )
                for k, d in [("video", snap.output.sha256), ("cover", snap.cover.sha256)]
            )
            return assemble_prepared(snap, values, "test-client", authority)

        def submit(self, prepared, permit, target):
            t = next(t for t in snapshot.targets if t.account.account_id == target)
            return SubmissionReceipt(
                target_id=target,
                state="SUBMITTED",
                provider_task_id="provider-" + target,
                platform_content_id="platform-" + target,
                platform=t.account.platform,
                snapshot_digest=snapshot.digest,
                video_sha256=snapshot.output.sha256,
                cover_sha256=snapshot.cover.sha256,
                evidence_origin="verified_platform",
                actual_channel="local",
            )

    permit = authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=datetime.now(UTC) + timedelta(minutes=3),
        max_uploads=0,
        max_submissions_per_target=1,
        authorization_note="Synthetic DB-only terminal test",
    )
    complete = coordinator.run_submission(
        snapshot.job_id,
        permit,
        LocalFake(),
        **controlled_verification(store, authority, snapshot, permit),
    )
    assert complete.state == "COMPLETED" and complete.content_frozen


@pytest.mark.parametrize("new_state,new_revision", [("CANCELED", False), ("READY", True)])
def test_startup_recovery_preserves_cancellation_and_new_content(
    publishing_context, coordination, new_state, new_revision
):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")

    class Crash(BaseException):
        pass

    def hook(point, target):
        if point == "after_attempt":
            raise Crash()

    coordinator.checkpoint_hook = hook
    with pytest.raises(Crash):
        coordinator.run_submission(
            snapshot.job_id, scope(authority, snapshot), FakePublisher(authority)
        )
    store = publishing_context[0]
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET state=?,owner_worker_id=NULL, "
            "content_revision=content_revision+? WHERE id=?",
            (new_state, int(new_revision), snapshot.job_id),
        )
    store.recover_interrupted()  # Worker startup runs this BEFORE publication recovery.
    coordinator.recover_interrupted()
    assert store.get(snapshot.job_id).state == new_state
    assert not store.get(snapshot.job_id).content_frozen
    assert coordinator.attempts(snapshot.digest, "simulation")[0].state == "UNKNOWN"
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM publication_source_claims").fetchone()[0] == 3


def test_durable_receipt_survives_crash_and_is_not_resubmitted(
    publishing_context, coordination, monkeypatch
):
    from fabu.publishing import coordinator as module
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    original = module.persist_receipt

    class Crash(BaseException):
        pass

    def crash_after_receipt(*args, **kwargs):
        original(*args, **kwargs)
        raise Crash()

    publisher = FakePublisher(authority)
    monkeypatch.setattr(module, "persist_receipt", crash_after_receipt)
    with pytest.raises(Crash):
        coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    monkeypatch.setattr(module, "persist_receipt", original)
    coordinator.recover_interrupted()
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert [c[1] for c in publisher.calls if c[0] == "submit"] == [
        t.account.account_id for t in snapshot.targets
    ]


def test_definite_failure_retry_limit_is_durable_across_permits(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority, outcomes=["FAILED"] * 4)
    for _ in range(4):
        coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert len([c for c in publisher.calls if c[0] == "submit"]) == 3
    assert len(coordinator.attempts(snapshot.digest, "simulation")) == 3


def test_uncertain_resource_upload_keeps_source_claim(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)

    def prepare(snap, permit):
        publisher.journal.begin(snap.digest, "video")
        raise TimeoutError("response lost")

    publisher.prepare = prepare
    coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert not coordinator.attempts(snapshot.digest, "simulation")
    with coordinator.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM publication_source_claims").fetchone()[0] == 3


def test_cancel_between_targets_keeps_prior_receipts_and_claims(publishing_context, coordination):
    from fabu.publishing.fake_gateway import FakePublisher

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    coordinator.register(snapshot, "simulation")
    publisher = FakePublisher(authority)

    def hook(point, target):
        if point == "after_submit":
            with coordinator.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET state='CANCELED',owner_worker_id=NULL WHERE id=?",
                    (snapshot.job_id,),
                )

    coordinator.checkpoint_hook = hook
    result = coordinator.run_submission(snapshot.job_id, scope(authority, snapshot), publisher)
    assert result.state == "CANCELED"
    assert [a.state for a in coordinator.attempts(snapshot.digest, "simulation")] == ["SUBMITTED"]
    with coordinator.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM publication_source_claims").fetchone()[0] == 3


@pytest.mark.parametrize("boundary", ["after_prepare", "after_submit"])
def test_schedule_expiry_after_work_never_falls_back_to_immediate(
    publishing_context, coordination, boundary
):
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.models import publish_snapshot_digest

    snapshot = build(publishing_context)
    coordinator, authority = coordination
    now = datetime.now(UTC)
    when = (now + timedelta(minutes=2)).replace(microsecond=0)
    snapshot = snapshot.model_copy(
        update={
            "mode": "native_schedule",
            "scheduled_at_utc": when,
            "targets": tuple(
                t.model_copy(
                    update={"form": {**t.form, "scheduledTime": int(when.timestamp() * 1000)}}
                )
                for t in snapshot.targets
            ),
        }
    )
    job = coordinator.store.get(snapshot.job_id)
    with coordinator.store.transaction() as db:
        db.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            (
                job.spec.model_copy(
                    update={"publish_mode": "native_schedule", "scheduled_at_utc": when}
                ).model_dump_json(),
                job.id,
            ),
        )
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    coordinator.register(snapshot, "simulation")
    permit = scope(authority, snapshot)

    def hook(point, target):
        if point == boundary:
            authority.clock = lambda: when + timedelta(seconds=1)
            coordinator.clock = authority.clock

    coordinator.checkpoint_hook = hook
    publisher = FakePublisher(authority)
    result = coordinator.run_submission(snapshot.job_id, permit, publisher)
    assert result.state == "PAUSED"
    attempts = coordinator.attempts(snapshot.digest, "simulation")
    assert len(attempts) == (1 if boundary == "after_submit" else 0)
    assert all(a.state == "SCHEDULED" for a in attempts)
    assert all(r.scheduled_at_utc == when for r in publisher.receipts.values())
