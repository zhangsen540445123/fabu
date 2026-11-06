"""Synthetic-only verification evidence; never upload or call a real publisher."""

from datetime import UTC, datetime, timedelta

import pytest
import test_gateway
from test_templates import build

gateway_setup = test_gateway.gateway_setup


@pytest.fixture
def verification_context(publishing_context):
    from fabu.publishing.models import publish_snapshot_digest
    from fabu.publishing.permits import PermitAuthority
    from fabu.publishing.verification import VerificationService

    now = datetime.now(UTC)
    authority = PermitAuthority(clock=lambda: now)
    snapshot = build(publishing_context).model_copy(update={"mode": "immediate"})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    permit = authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=now + timedelta(minutes=10),
        max_uploads=2,
        max_submissions_per_target=1,
        allow_temporary_storage_upload=True,
        authorization_note="Synthetic exact snapshot: three targets, one try each, no transport",
    )
    service = VerificationService(publishing_context[0], authority)
    return service, authority, snapshot, permit


def safety(context):
    service, authority, snapshot, permit = context
    return {
        "kind": "safety",
        "observed_at": authority.clock(),
        "artifact_sha256": "a" * 64,
        "expires_at": permit.expires_at,
        "cli_sha256": "b" * 64,
        "client_sha256": "c" * 64,
        "target_ids": list(permit.target_ids),
        "free_units_remaining": 10,
        "max_upload_debit": 1,
        "max_target_debits": {target: 1 for target in permit.target_ids},
        "max_cost_cents": 0,
        "automatic_purchase": False,
        "max_video_bytes": snapshot.output.size_bytes + 1,
        "max_cover_bytes": snapshot.cover.size_bytes + 1,
        "free_storage_bytes": snapshot.output.size_bytes + snapshot.cover.size_bytes + 1,
        "resources_valid_until": permit.expires_at + timedelta(days=1),
        "reset_at": permit.expires_at + timedelta(hours=1),
        "failed_attempts_included": True,
        "route": "local",
        "submission_strategy": "per_target",
    }


def report(context):
    from fabu.publishing.capabilities import CapabilityReport

    return CapabilityReport(
        checked_at=context[1].clock(),
        tool_version="3.2.32",
        account_status="SUPPORTED",
        source_declarations="SUPPORTED",
        categories="SUPPORTED",
        quota="SUPPORTED",
        temporary_storage="SUPPORTED",
        file_limits="SUPPORTED",
        submission_strategy="per_target",
    )


def test_unknown_write_capability_can_be_tested_but_not_auto_enabled(verification_context):
    service, authority, snapshot, permit = verification_context
    evidence_id = service.record(permit, snapshot, safety(verification_context))
    gate = service.controlled_gate(permit, snapshot, evidence_id)
    assert service.allow_controlled(gate, permit, snapshot, report(verification_context))
    result = service.verify_controlled_run(permit, snapshot, [evidence_id])
    assert result.immediate == "UNKNOWN"
    assert not service.grant_production_mode(result, list(permit.target_ids), "immediate")


@pytest.mark.parametrize(
    "field",
    ["target_ids", "snapshot_digest", "max_uploads", "max_submissions_per_target", "expires_at"],
)
def test_controlled_permit_cannot_expand_scope(verification_context, field):
    service, authority, snapshot, permit = verification_context
    identifier = service.record(permit, snapshot, safety(verification_context))
    changed = permit.model_copy(
        update={
            field: {
                "target_ids": ("outside",),
                "snapshot_digest": "f" * 64,
                "max_uploads": 9,
                "max_submissions_per_target": 3,
                "expires_at": permit.expires_at + timedelta(days=1),
            }[field]
        }
    )
    with pytest.raises(ValueError):
        service.controlled_gate(changed, snapshot, identifier)


@pytest.mark.parametrize(
    "change",
    [
        {"free_units_remaining": 1},
        {"max_cost_cents": 1},
        {"automatic_purchase": True},
        {"failed_attempts_included": False},
        {"max_target_debits": {}},
        {"route": "cloud"},
    ],
)
def test_unproven_or_paid_cost_boundary_cannot_authorize_a_test(verification_context, change):
    service, authority, snapshot, permit = verification_context
    with pytest.raises(ValueError):
        identifier = service.record(permit, snapshot, {**safety(verification_context), **change})
        service.controlled_gate(permit, snapshot, identifier)


def test_controlled_gate_binds_cli_client_and_rechecks_expiry(verification_context):
    service, authority, snapshot, permit = verification_context
    identifier = service.record(permit, snapshot, safety(verification_context))
    gate = service.controlled_gate(permit, snapshot, identifier)
    assert not service.allow_controlled(
        gate.model_copy(update={"cli_sha256": "f" * 64}),
        permit,
        snapshot,
        report(verification_context),
    )
    authority.clock = lambda: permit.expires_at + timedelta(seconds=1)
    assert not service.allow_controlled(gate, permit, snapshot, report(verification_context))


def test_browser_shaped_capability_report_is_not_a_verified_grant(verification_context):
    service, authority, snapshot, permit = verification_context
    forged = report(verification_context).model_copy(
        update={
            "upload": "SUPPORTED",
            "hevc": "SUPPORTED",
            "immediate": "SUPPORTED",
            "mode_evidence": {t: ["immediate"] for t in permit.target_ids},
        }
    )
    assert not service.grant_production_mode(forged, list(permit.target_ids), "immediate")


def test_evidence_is_private_and_snapshot_bound(verification_context):
    from fabu.publishing.models import publish_snapshot_digest

    service, authority, snapshot, permit = verification_context
    identifier = service.record(permit, snapshot, safety(verification_context))
    with service.store.connection() as db:
        row = db.execute("SELECT * FROM verification_evidence WHERE id=?", (identifier,)).fetchone()
    assert b'"free_units_remaining"' not in bytes(row["evidence_cipher"])
    other = snapshot.model_copy(
        update={
            "title": "different",
            "targets": tuple(
                t.model_copy(update={"form": {**t.form, "title": "different"}})
                for t in snapshot.targets
            ),
        }
    )
    other = other.model_copy(update={"digest": publish_snapshot_digest(other)})
    with pytest.raises(ValueError):
        service.verify_controlled_run(permit, other, [identifier])


def complete_evidence(context, target_state="PUBLISHED", *, persist=True):
    from fabu.publishing.verification import metadata_digest

    service, authority, snapshot, permit = context
    values = [safety(context)]
    import hashlib

    remaining = 10
    for kind, item in [("video", snapshot.output), ("cover", snapshot.cover)]:
        values.append(
            {
                "kind": "upload",
                "resource": kind,
                "observed_at": authority.clock(),
                "artifact_sha256": "d" * 64,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "provider_resource_sha256": hashlib.sha256(
                    ("cloud-publish/synthetic/" + kind).encode()
                ).hexdigest(),
                "expires_at": permit.expires_at + timedelta(days=1),
                "unchanged": True,
                "reuse_verified": True,
            }
        )
        values.append(
            {
                "kind": "quota",
                "operation": "upload:" + kind,
                "observed_at": authority.clock(),
                "artifact_sha256": "d" * 64,
                "free_before": remaining,
                "free_after": remaining - 1,
                "paid_debit_cents": 0,
                "counter_epoch": "one-synthetic-day",
                "isolated": True,
            }
        )
        remaining -= 1
    for target in snapshot.targets:
        values.append(
            {
                "kind": "target",
                "observed_at": authority.clock(),
                "artifact_sha256": "d" * 64,
                "metadata_digest": metadata_digest(target),
                "receipt": {
                    "target_id": target.account.account_id,
                    "state": target_state,
                    "provider_task_id": "provider-" + target.account.account_id,
                    "platform_content_id": "content-" + target.account.account_id,
                    "actual_channel": "local",
                    "snapshot_digest": snapshot.digest,
                    "video_sha256": snapshot.output.sha256,
                    "cover_sha256": snapshot.cover.sha256,
                    "platform": target.account.platform,
                    "evidence_origin": "verified_platform",
                },
            }
        )
        values.append(
            {
                "kind": "quota",
                "operation": "submit:" + target.account.account_id,
                "observed_at": authority.clock(),
                "artifact_sha256": "d" * 64,
                "free_before": remaining,
                "free_after": remaining - 1,
                "paid_debit_cents": 0,
                "counter_epoch": "one-synthetic-day",
                "isolated": True,
            }
        )
        remaining -= 1
    if persist:
        seed_durable(context, values)
    return values


def seed_durable(context, values):
    from uuid import uuid4

    from fabu.domain import TargetAttempt
    from fabu.publishing.gateway import SubmissionReceipt, UploadReceipt, resource_metadata
    from fabu.publishing.journal import SqliteUploadJournal
    from fabu.publishing.reconcile import persist_receipt

    service, authority, snapshot, permit = context
    now = authority.clock().isoformat()
    run_id = str(uuid4())
    with service.store.transaction() as db:
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
    journal = SqliteUploadJournal(service.store, snapshot, scope="live")
    for kind, item in [("video", snapshot.output), ("cover", snapshot.cover)]:
        journal.begin(snapshot.digest, kind)
        journal.complete(
            snapshot.digest,
            UploadReceipt(
                resource=kind,
                sha256=item.sha256,
                key="cloud-publish/synthetic/" + kind,
                metadata=resource_metadata(snapshot, kind),
                raw={"synthetic": True},
            ),
        )
    for index, value in enumerate(v for v in values if v["kind"] == "target"):
        receipt = SubmissionReceipt.model_validate(value["receipt"])
        attempt = TargetAttempt(
            job_id=snapshot.job_id,
            revision=snapshot.content_revision,
            target_id=receipt.target_id,
            payload_digest=snapshot.digest,
            state="SUBMITTING",
        )
        service.store.record_attempt(attempt)
        with service.store.transaction() as db:
            db.execute(
                "INSERT INTO publication_attempt_links VALUES (?,?,?,NULL,0)",
                (attempt.id, run_id, index),
            )
        persist_receipt(service.store, attempt, snapshot, "live", receipt)


def test_complete_bound_evidence_only_grants_exact_accounts_and_mode(verification_context):
    service, authority, snapshot, permit = verification_context
    ids = [service.record(permit, snapshot, v) for v in complete_evidence(verification_context)]
    report = service.verify_controlled_run(permit, snapshot, ids)
    assert report.immediate == "SUPPORTED" and report.native_schedule == "UNKNOWN"
    assert service.grant_production_mode(report, list(permit.target_ids), "immediate")
    assert not service.grant_production_mode(report, ["outside"], "immediate")
    assert not service.grant_production_mode(report, list(permit.target_ids), "native_schedule")
    assert not service.grant_production_mode(
        report.model_copy(update={"file_limits": "UNKNOWN"}), list(permit.target_ids), "immediate"
    )


@pytest.mark.parametrize("state", ["FAILED", "UNKNOWN", "PROVIDER_ACCEPTED", "SUBMITTED"])
def test_failed_run_does_not_grant_production(verification_context, state):
    service, authority, snapshot, permit = verification_context
    ids = [
        service.record(permit, snapshot, v) for v in complete_evidence(verification_context, state)
    ]
    report = service.verify_controlled_run(permit, snapshot, ids)
    assert not service.grant_production_mode(report, list(permit.target_ids), "immediate")


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_metadata",
        "missing_cover",
        "paid_debit",
        "quota_gap",
        "changed_upload",
        "future_evidence",
        "duplicate_quota",
        "wrong_account",
    ],
)
def test_incomplete_or_mismatched_evidence_never_grants(verification_context, damage):
    service, authority, snapshot, permit = verification_context
    values = complete_evidence(verification_context)
    if damage == "wrong_metadata":
        values[5]["metadata_digest"] = "0" * 64
    if damage == "missing_cover":
        values = [v for v in values if v.get("resource") != "cover"]
    if damage == "paid_debit":
        values[2]["paid_debit_cents"] = 1
    if damage == "quota_gap":
        values[4]["free_before"] = 999
    if damage == "changed_upload":
        values[1]["sha256"] = "0" * 64
    if damage == "future_evidence":
        values[1]["observed_at"] = permit.expires_at + timedelta(days=1)
    if damage == "duplicate_quota":
        values.append(values[2])
    if damage == "wrong_account":
        values[5]["receipt"]["target_id"] = "outside"
    try:
        ids = [service.record(permit, snapshot, v) for v in values]
        report = service.verify_controlled_run(permit, snapshot, ids)
    except ValueError:
        return
    assert not service.grant_production_mode(report, list(permit.target_ids), "immediate")


def test_coordinator_accepts_only_signed_controlled_gate_for_unknown_capabilities(
    publishing_context, verification_context
):
    from fabu.publishing.coordinator import PublicationCoordinator
    from fabu.publishing.gateway import (
        SubmissionReceipt,
        UploadReceipt,
        assemble_prepared,
        resource_metadata,
    )
    from fabu.worker import Worker

    service, authority, snapshot, permit = verification_context
    store, assets, presets, approvals = publishing_context[:4]
    job = store.get(snapshot.job_id)
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            (job.spec.model_copy(update={"publish_mode": "immediate"}).model_dump_json(), job.id),
        )

    class LocalOnly:
        execution_scope = "live"
        calls = []

        def preflight(self, snap):
            return report(verification_context)

        def prepare(self, snap, permission):
            self.calls.append("prepare")
            resources = tuple(
                UploadReceipt(
                    resource=k,
                    sha256=item.sha256,
                    key="cloud-publish/synthetic/" + k,
                    metadata=resource_metadata(snap, k),
                    raw={},
                )
                for k, item in [("video", snap.output), ("cover", snap.cover)]
            )
            return assemble_prepared(snap, resources, "synthetic-client", authority)

        def submit(self, prepared, permission, target):
            self.calls.append(target)
            return SubmissionReceipt(target_id=target, state="UNKNOWN")

    with Worker(store) as worker:
        coordinator = PublicationCoordinator(store, assets, presets, approvals, authority, worker)
        coordinator.register(snapshot, "live")
        publisher = LocalOnly()
        result = coordinator.run_submission(job.id, permit, publisher)
        assert result.state == "PAUSED" and publisher.calls == []
        identifier = service.record(permit, snapshot, safety(verification_context))
        gate = service.controlled_gate(permit, snapshot, identifier)
        result = coordinator.run_submission(
            job.id, permit, publisher, verification=service, controlled_gate=gate
        )
        assert result.state == "PAUSED" and publisher.calls == ["prepare", permit.target_ids[0]]


def test_required_storage_and_category_capabilities_cannot_be_skipped(verification_context):
    from fabu.publishing.capabilities import can_enable_mode

    base = report(verification_context).model_copy(
        update={
            "upload": "SUPPORTED",
            "hevc": "SUPPORTED",
            "immediate": "SUPPORTED",
            "mode_evidence": {t: ["immediate"] for t in verification_context[3].target_ids},
        }
    )
    assert can_enable_mode(base, "immediate", list(verification_context[3].target_ids))
    for key in ["temporary_storage", "categories"]:
        assert not can_enable_mode(
            base.model_copy(update={key: "UNKNOWN"}),
            "immediate",
            list(verification_context[3].target_ids),
        )


def test_official_cli_controlled_preflight_checks_binary_and_client_identity(gateway_setup):
    import hashlib

    from fabu.publishing.models import publish_snapshot_digest
    from fabu.publishing.verification import VerificationService
    from fabu.store import JobStore

    old, gateway, authority, calls, responses = gateway_setup
    snapshot = old.model_copy(update={"mode": "immediate"})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    permit = authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=authority.clock() + timedelta(minutes=3),
        max_uploads=2,
        max_submissions_per_target=1,
        allow_temporary_storage_upload=True,
        authorization_note="Synthetic CLI identity test only",
    )
    service = VerificationService(
        JobStore(gateway.workspace.parent / "verification.sqlite3"), authority
    )
    context = (service, authority, snapshot, permit)
    boundary = safety(context)
    boundary["cli_sha256"] = hashlib.sha256(gateway.executable.read_bytes()).hexdigest()
    boundary["client_sha256"] = hashlib.sha256(b"synthetic-client").hexdigest()
    identifier = service.record(permit, snapshot, boundary)
    gate = service.controlled_gate(permit, snapshot, identifier)
    gateway.bind_controlled_test(service, gate, permit, snapshot)
    base = report(context)
    assert (
        service.prepare_controlled_report(
            gate,
            permit,
            snapshot,
            base,
            cli_sha256=boundary["cli_sha256"],
            client_sha256=boundary["client_sha256"],
        ).quota
        == "SUPPORTED"
    )
    with pytest.raises(ValueError):
        service.prepare_controlled_report(
            gate,
            permit,
            snapshot,
            base,
            cli_sha256="0" * 64,
            client_sha256=boundary["client_sha256"],
        )
    gateway.executable.write_bytes(b"changed-binary")
    with pytest.raises(ValueError):
        gateway.prepare(snapshot, permit)
    assert not calls


def test_fact_only_without_durable_publication_record_cannot_grant(verification_context):
    service, authority, snapshot, permit = verification_context
    ids = [
        service.record(permit, snapshot, v)
        for v in complete_evidence(verification_context, persist=False)
    ]
    result = service.verify_controlled_run(permit, snapshot, ids, require_durable=True)
    assert not service.grant_production_mode(result, list(permit.target_ids), "immediate")
    assert "durable_attempts_unverified" in result.blockers


def test_controlled_call_deadline_does_not_outlive_permit(gateway_setup):
    import hashlib

    from fabu.publishing.models import publish_snapshot_digest
    from fabu.publishing.verification import VerificationService
    from fabu.store import JobStore

    old, gateway, authority, calls, responses = gateway_setup
    snapshot = old.model_copy(update={"mode": "immediate"})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    permit = authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=authority.clock() + timedelta(seconds=30),
        max_uploads=2,
        max_submissions_per_target=1,
        allow_temporary_storage_upload=True,
        authorization_note="Synthetic bounded timeout",
    )
    service = VerificationService(JobStore(gateway.workspace.parent / "limits.sqlite3"), authority)
    values = safety((service, authority, snapshot, permit))
    values["cli_sha256"] = hashlib.sha256(gateway.executable.read_bytes()).hexdigest()
    values["client_sha256"] = hashlib.sha256(b"synthetic-client").hexdigest()
    identifier = service.record(permit, snapshot, values)
    gate = service.controlled_gate(permit, snapshot, identifier)
    gateway.bind_controlled_test(service, gate, permit, snapshot)
    gateway._call(["upload", "synthetic-path", "--json"], timeout=86400)
    assert 0 < calls[0].timeout_s <= 30


def test_unknown_cached_resource_lifetime_cannot_be_reused(verification_context):
    from fabu.publishing.gateway import UploadReceipt, resource_metadata

    service, authority, snapshot, permit = verification_context
    identifier = service.record(permit, snapshot, safety(verification_context))
    gate = service.controlled_gate(permit, snapshot, identifier)
    receipt = UploadReceipt(
        resource="video",
        sha256=snapshot.output.sha256,
        key="cloud-publish/synthetic/video",
        metadata=resource_metadata(snapshot, "video"),
        raw={},
    )
    assert not service.resource_reusable(gate, permit, snapshot, receipt)
    item = complete_evidence(verification_context, persist=False)[1]
    service.record(permit, snapshot, item)
    assert service.resource_reusable(gate, permit, snapshot, receipt)
    authority.clock = lambda: permit.expires_at + timedelta(days=2)
    assert not service.resource_reusable(gate, permit, snapshot, receipt)
