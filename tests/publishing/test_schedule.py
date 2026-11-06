from datetime import UTC, datetime, timedelta

import pytest
import test_controlled_verification

verification_context = test_controlled_verification.verification_context


def receipt():
    from fabu.publishing.gateway import SubmissionReceipt

    return SubmissionReceipt(
        target_id="bili",
        state="PROVIDER_ACCEPTED",
        provider_task_id="provider-id",
        actual_channel="local",
        snapshot_digest="a" * 64,
        video_sha256="b" * 64,
        cover_sha256="c" * 64,
        platform="bilibili",
    )


def observation(now, when, **updates):
    return {
        "target_id": "bili",
        "platform": "bilibili",
        "provider_task_id": "provider-id",
        "platform_content_id": "BV-synthetic",
        "snapshot_digest": "a" * 64,
        "video_sha256": "b" * 64,
        "cover_sha256": "c" * 64,
        "scheduled_at_utc": when,
        "observed_at": now,
        "execution_owner": "platform",
        "status": "SCHEDULED",
        "published_at_utc": None,
        "client_independence_verified": False,
        "artifact_sha256": "d" * 64,
        **updates,
    }


def test_one_platform_window_blocks_all_before_submit():
    from fabu.publishing.capabilities import CapabilityReport, ScheduleWindow
    from fabu.publishing.schedule import validate_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    when = now + timedelta(hours=2)
    report = CapabilityReport(
        checked_at=now,
        schedule_windows={
            "dy": ScheduleWindow(
                earliest_utc=now + timedelta(hours=3), latest_utc=now + timedelta(days=1)
            ),
            "bili": ScheduleWindow(earliest_utc=now, latest_utc=now + timedelta(days=1)),
        },
    )
    issues = validate_schedule(["dy", "bili"], when, report, now)
    assert [(v.target_id, v.code) for v in issues] == [("dy", "outside_platform_window")]
    assert validate_schedule(["missing"], when, report, now)[0].code == "window_unverified"


def test_provider_queue_not_native_schedule():
    from fabu.publishing.schedule import verify_native_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    when = now + timedelta(hours=1)
    result = verify_native_schedule(
        receipt(), [observation(now, when, execution_owner="provider_queue")], when, now=now
    )
    assert result.state == "UNKNOWN"


@pytest.mark.parametrize(
    "change",
    [
        {"target_id": "other"},
        {"provider_task_id": "wrong"},
        {"video_sha256": "0" * 64},
        {"cover_sha256": "0" * 64},
        {"platform": "douyin"},
        {"snapshot_digest": "0" * 64},
    ],
)
def test_wrong_timestamp_or_account_is_unknown(change):
    from fabu.publishing.schedule import verify_native_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    when = now + timedelta(hours=1)
    assert (
        verify_native_schedule(receipt(), [observation(now, when, **change)], when, now=now).state
        == "UNKNOWN"
    )
    assert (
        verify_native_schedule(
            receipt(), [observation(now, when + timedelta(seconds=1))], when, now=now
        ).state
        == "UNKNOWN"
    )


def test_clock_passage_does_not_mean_published():
    from fabu.publishing.schedule import verify_native_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    when = now - timedelta(hours=1)
    result = verify_native_schedule(receipt(), [observation(now, when)], when, now=now)
    assert result.state == "SCHEDULED" and result.platform_content_id == "BV-synthetic"


def test_platform_publication_requires_actual_final_observation():
    from fabu.publishing.schedule import can_exit_client, verify_native_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    when = now - timedelta(minutes=2)
    value = observation(
        now, when, status="PUBLISHED", published_at_utc=when + timedelta(seconds=20)
    )
    result = verify_native_schedule(receipt(), [value], when, now=now)
    assert result.state == "PUBLISHED" and result.evidence_origin == "verified_platform"
    assert not can_exit_client(receipt(), [value], when, now=now)
    assert can_exit_client(
        receipt(), [{**value, "client_independence_verified": True}], when, now=now
    )
    assert not can_exit_client(
        receipt(),
        [{**value, "execution_owner": "provider_queue", "client_independence_verified": True}],
        when,
        now=now,
    )


def test_future_publication_and_conflicting_observations_stay_unknown():
    from fabu.publishing.schedule import verify_native_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    when = now + timedelta(hours=1)
    value = observation(now, when, status="PUBLISHED", published_at_utc=when)
    assert verify_native_schedule(receipt(), [value], when, now=now).state == "UNKNOWN"
    scheduled = observation(now, when)
    assert (
        verify_native_schedule(
            receipt(), [scheduled, {**scheduled, "status": "FAILED"}], when, now=now
        ).state
        == "UNKNOWN"
    )


def test_naive_expired_or_stale_schedule_is_rejected_without_conversion():
    from fabu.publishing.capabilities import CapabilityReport
    from fabu.publishing.schedule import validate_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    report = CapabilityReport(checked_at=now - timedelta(hours=1))
    assert (
        validate_schedule(["bili"], now + timedelta(hours=1), report, now)[0].code
        == "preflight_expired"
    )
    assert (
        validate_schedule(["bili"], now.replace(tzinfo=None), report, now)[0].code
        == "timezone_required"
    )
    assert validate_schedule(["bili"], now, report, now)[0].code == "schedule_expired"


@pytest.mark.parametrize("has_native_proof", [False, True])
def test_native_mode_needs_platform_custody_and_final_evidence(
    verification_context, has_native_proof
):
    from test_controlled_verification import complete_evidence

    from fabu.publishing.models import publish_snapshot_digest

    service, authority, old, old_permit = verification_context
    when = (authority.clock() + timedelta(minutes=1)).replace(microsecond=0)
    snapshot = old.model_copy(
        update={
            "mode": "native_schedule",
            "scheduled_at_utc": when,
            "targets": tuple(
                t.model_copy(
                    update={"form": {**t.form, "scheduledTime": int(when.timestamp() * 1000)}}
                )
                for t in old.targets
            ),
        }
    )
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    permit = authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=authority.clock() + timedelta(seconds=30),
        max_uploads=2,
        max_submissions_per_target=1,
        allow_temporary_storage_upload=True,
        authorization_note="Synthetic native schedule",
    )
    context = (service, authority, snapshot, permit)
    values = complete_evidence(context, persist=False)
    # Upload/quota observations occur while the permit is valid; final platform reads happen later.
    published = when + timedelta(seconds=15)
    for value in values:
        if value["kind"] == "target":
            value["observed_at"] = published
            value["receipt"]["scheduled_at_utc"] = when
            if has_native_proof:
                r = value["receipt"]
                value["native_schedule"] = {
                    **{
                        k: r[k]
                        for k in [
                            "target_id",
                            "platform",
                            "provider_task_id",
                            "platform_content_id",
                            "snapshot_digest",
                            "video_sha256",
                            "cover_sha256",
                        ]
                    },
                    "artifact_sha256": "e" * 64,
                    "scheduled_at_utc": when,
                    "observed_at": published,
                    "execution_owner": "platform",
                    "status": "PUBLISHED",
                    "published_at_utc": published,
                    "client_independence_verified": True,
                }
    from test_controlled_verification import seed_durable

    seed_durable(context, values)
    authority.clock = lambda: published
    ids = [service.record(permit, snapshot, v) for v in values]
    result = service.verify_controlled_run(permit, snapshot, ids)
    assert result.native_schedule == ("SUPPORTED" if has_native_proof else "UNKNOWN")
    assert not service.grant_production_mode(
        result, list(permit.target_ids), "native_schedule"
    )  # expired execution/cost boundary cannot grant fresh writes


def test_platform_failure_after_scheduling_is_recorded_not_retried(publishing_context):
    from uuid import uuid4

    from test_templates import build

    from fabu.domain import TargetAttempt
    from fabu.publishing.gateway import SubmissionReceipt
    from fabu.publishing.models import publish_snapshot_digest
    from fabu.publishing.reconcile import persist_receipt

    snapshot = build(publishing_context)
    target = snapshot.targets[0]
    when = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
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
    store = publishing_context[0]
    now = datetime.now(UTC).isoformat()
    run_id = str(uuid4())
    with store.transaction() as db:
        db.execute(
            "INSERT INTO publication_runs VALUES (?,?,?,?,?,?,'COMPLETE',NULL,NULL,NULL,?,?)",
            (
                run_id,
                snapshot.job_id,
                snapshot.content_revision,
                snapshot.digest,
                snapshot.model_dump_json(),
                "live",
                now,
                now,
            ),
        )
    attempt = TargetAttempt(
        job_id=snapshot.job_id,
        revision=snapshot.content_revision,
        target_id=target.account.account_id,
        payload_digest=snapshot.digest,
        state="SUBMITTING",
    )
    store.record_attempt(attempt)
    with store.transaction() as db:
        db.execute(
            "INSERT INTO publication_attempt_links VALUES (?,?,0,NULL,0)", (attempt.id, run_id)
        )
    scheduled = SubmissionReceipt(
        target_id=attempt.target_id,
        state="SCHEDULED",
        provider_task_id="provider-id",
        platform_content_id="content-id",
        scheduled_at_utc=when,
        snapshot_digest=snapshot.digest,
        video_sha256=snapshot.output.sha256,
        cover_sha256=snapshot.cover.sha256,
        platform=target.account.platform,
        evidence_origin="verified_platform",
    )
    persist_receipt(store, attempt, snapshot, "live", scheduled)
    attempt = store.attempts(snapshot.job_id)[0]
    failed = scheduled.model_copy(update={"state": "FAILED", "definitely_not_submitted": False})
    persist_receipt(store, attempt, snapshot, "live", failed, from_reconcile=True)
    assert store.attempts(snapshot.job_id)[0].state == "FAILED"
    with store.connection() as db:
        assert (
            db.execute("SELECT definitely_not_submitted FROM publication_attempt_links").fetchone()[
                0
            ]
            == 0
        )


def test_minimum_lead_time_moves_forward_during_upload():
    from fabu.publishing.capabilities import CapabilityReport, ScheduleWindow
    from fabu.publishing.schedule import validate_schedule

    now = datetime.now(UTC).replace(microsecond=0)
    report = CapabilityReport(
        checked_at=now,
        schedule_windows={
            "bili": ScheduleWindow(
                earliest_utc=now, latest_utc=now + timedelta(days=1), minimum_lead_seconds=120
            )
        },
    )
    when = now + timedelta(seconds=150)
    assert not validate_schedule(["bili"], when, report, now)
    assert (
        validate_schedule(["bili"], when, report, now + timedelta(seconds=45))[0].code
        == "outside_platform_window"
    )


def test_controlled_preflight_uses_scoped_fresh_native_window_evidence(verification_context):
    from test_controlled_verification import report, safety

    from fabu.publishing.models import publish_snapshot_digest

    service, authority, old, _ = verification_context
    now = authority.clock()
    when = (now + timedelta(hours=1)).replace(microsecond=0)
    snapshot = old.model_copy(
        update={
            "mode": "native_schedule",
            "scheduled_at_utc": when,
            "targets": tuple(
                t.model_copy(
                    update={"form": {**t.form, "scheduledTime": int(when.timestamp() * 1000)}}
                )
                for t in old.targets
            ),
        }
    )
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    permit = authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=now + timedelta(minutes=5),
        max_uploads=2,
        max_submissions_per_target=1,
        allow_temporary_storage_upload=True,
        authorization_note="Synthetic verified windows",
    )
    context = (service, authority, snapshot, permit)
    boundary = safety(context)
    identifier = service.record(permit, snapshot, boundary)
    gate = service.controlled_gate(permit, snapshot, identifier)
    base = report(context)
    for target in permit.target_ids:
        service.record(
            permit,
            snapshot,
            {
                "kind": "window",
                "observed_at": now,
                "artifact_sha256": "f" * 64,
                "target_id": target,
                "earliest_utc": now + timedelta(minutes=2),
                "latest_utc": now + timedelta(days=1),
                "minimum_lead_seconds": 120,
                "expires_at": permit.expires_at,
                "platform_native": True,
            },
        )
    updated = service.prepare_controlled_report(
        gate,
        permit,
        snapshot,
        base,
        cli_sha256=boundary["cli_sha256"],
        client_sha256=boundary["client_sha256"],
    )
    assert set(updated.schedule_windows) == set(permit.target_ids)
    assert service.allow_controlled(gate, permit, snapshot, updated)
    assert not service.allow_controlled(gate, permit, snapshot, base)
