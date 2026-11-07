"""Contract-accurate no-network transport; never a live-publication acceptance test."""

import json

import pytest
import test_gateway as gateway_fixtures
import test_local_policy as policy_fixtures
from test_local_policy import authorize_fixture

local_setup = policy_fixtures.local_setup
gateway_setup = gateway_fixtures.gateway_setup


def prepared_job(local_setup, publishing_context, monkeypatch):
    from fabu.publishing.templates import build_publish_snapshot

    policy, app, old_snapshot, gateway = local_setup
    policy.enable()
    scoped = authorize_fixture(policy, app, old_snapshot)
    store, assets, presets, approvals, old, output, config = publishing_context
    with store.transaction() as db:
        oldrow = db.execute(
            "SELECT source_json,plan_digest,output_json FROM jobs WHERE id=?", (old.id,)
        ).fetchone()
        db.execute(
            "UPDATE jobs SET state='READY',source_json=?,plan_digest=?,output_json=? WHERE id=?",
            (*oldrow, scoped.job_id),
        )
        db.execute(
            "INSERT INTO processing_snapshots SELECT ?,content_revision,preset_version_id,"
            "source_json,plan_json,created_at FROM processing_snapshots WHERE job_id=?",
            (scoped.job_id, old.id),
        )
    monkeypatch.setattr(approvals, "approval_matches", lambda *args: False)
    snapshot = build_publish_snapshot(
        store.get(scoped.job_id),
        output,
        config,
        store=store,
        assets=assets,
        presets=presets,
        approvals=approvals,
        require_preview=False,
    )
    # Structural fixtures have no actual encoding; validated metadata is tested separately.
    return policy, app, snapshot, gateway


def transport(gateway, snapshot, fail_target=None):
    from fabu.publishing.gateway import resource_metadata

    original = gateway.runner

    def run(spec, cancel):
        result = original(spec, cancel)
        raw = json.loads(result.stdout)
        if spec.args[0] == "accounts":
            raw["data"] = [
                {"id": t.account.account_id, "platform": t.account.platform, "status": 1}
                for t in snapshot.targets
            ]
        if spec.args[0] == "upload":
            kind = "video" if spec.args[1] == str(snapshot.output.path) else "cover"
            raw["data"] = {
                **resource_metadata(snapshot, kind),
                "key": f"yfb/test/{kind}",
                "bucket": "cloud-publish",
            }
        if spec.args[0] == "publish" and "--dry-run" not in spec.args:
            payload = json.loads(
                __import__("pathlib").Path(spec.args[3]).read_text(encoding="utf-8")
            )
            target_id = payload["publishArgs"]["accountForms"][0]["platformAccountId"]
            raw["data"]["taskSetId"] = "task-" + target_id
            if target_id == fail_target:
                return result.model_copy(update={"timed_out": True})
        return result.model_copy(update={"stdout": json.dumps(raw).encode()})

    gateway.runner = run


def test_three_distinct_acceptances_continue_without_sample_or_false_success(
    local_setup, publishing_context, monkeypatch
):
    from fabu.publishing.coordinator import PublicationCoordinator
    from fabu.worker import Worker

    policy, app, snapshot, gateway = prepared_job(local_setup, publishing_context, monkeypatch)
    transport(gateway, snapshot)
    store, assets, presets, approvals, *_ = publishing_context
    with Worker(store) as worker:
        coord = PublicationCoordinator(
            store, assets, presets, approvals, policy.authority, worker, local_policy=policy
        )
        coord.register(snapshot, "live")
        permit = policy.issue(snapshot)
        gateway.bind_user_job(policy, permit, snapshot)
        coord.run_submission(snapshot.job_id, permit, gateway)
        attempts = coord.attempts(snapshot.digest, "live")
        assert len(attempts) == 3 and {a.state for a in attempts} == {"PROVIDER_ACCEPTED"}
        assert len({a.provider_task_id for a in attempts}) == 3
        assert coord.status(snapshot.digest, "live")["reason"] == "provider_tracking_pending"
        with pytest.raises(ValueError):
            coord.run_submission(snapshot.job_id, permit, gateway)
    assert policy.remaining_today() == 0


def test_uncertain_second_submission_stops_third_and_never_retries(
    local_setup, publishing_context, monkeypatch
):
    from fabu.publishing.coordinator import PublicationCoordinator
    from fabu.worker import Worker

    policy, app, snapshot, gateway = prepared_job(local_setup, publishing_context, monkeypatch)
    transport(gateway, snapshot, snapshot.targets[1].account.account_id)
    store, assets, presets, approvals, *_ = publishing_context
    with Worker(store) as worker:
        coord = PublicationCoordinator(
            store, assets, presets, approvals, policy.authority, worker, local_policy=policy
        )
        coord.register(snapshot, "live")
        permit = policy.issue(snapshot)
        gateway.bind_user_job(policy, permit, snapshot)
        coord.run_submission(snapshot.job_id, permit, gateway)
        assert [a.state for a in coord.attempts(snapshot.digest, "live")] == [
            "PROVIDER_ACCEPTED",
            "UNKNOWN",
        ]
        with pytest.raises(ValueError):
            coord.run_submission(snapshot.job_id, permit, gateway)


def test_actual_runner_identity_uses_user_authority_without_controlled_gate(
    local_setup, publishing_context, monkeypatch
):
    from fabu.publishing import cli_gateway

    policy, app, snapshot, gateway = prepared_job(local_setup, publishing_context, monkeypatch)
    transport(gateway, snapshot)
    # Keep production `runner is run_command` checks active, while the boundary is offline.
    monkeypatch.setattr(cli_gateway, "run_command", gateway.runner)
    permit = policy.issue(snapshot)
    gateway.bind_user_job(policy, permit, snapshot)
    receipt = gateway._upload(snapshot, permit, "video")
    assert receipt.raw["bucket"] == "cloud-publish"


@pytest.mark.parametrize(
    "change", [{"duration": 0.01}, {"bucket": "material-library"}, {"duration": None}]
)
def test_cached_old_remote_metadata_cannot_bypass_new_integrity_checks(
    local_setup, publishing_context, monkeypatch, change
):
    from fabu.publishing.gateway import UploadReceipt, resource_metadata

    policy, app, snapshot, gateway = prepared_job(local_setup, publishing_context, monkeypatch)
    permit = policy.issue(snapshot)
    gateway.bind_user_job(policy, permit, snapshot)
    metadata = resource_metadata(snapshot, "video")
    receipt = UploadReceipt(
        resource="video",
        sha256=snapshot.output.sha256,
        key="yfb/test/old",
        metadata=metadata,
        raw={**metadata, "key": "yfb/test/old", "bucket": "cloud-publish", **change},
    )
    gateway.journal.begin(snapshot.digest, "video")
    gateway.journal.complete(snapshot.digest, receipt)
    with pytest.raises(ValueError):
        gateway._upload(snapshot, permit, "video")


def test_cancel_before_process_start_does_not_leave_unknown_upload(
    local_setup, publishing_context, monkeypatch
):
    from fabu.publishing.journal import SqliteUploadJournal

    policy, app, snapshot, gateway = prepared_job(local_setup, publishing_context, monkeypatch)
    permit = policy.issue(snapshot)
    gateway.bind_user_job(policy, permit, snapshot)
    gateway.journal = SqliteUploadJournal(app.state.store, snapshot, scope="live")
    begin = gateway.journal.begin

    def cancel_after_journal(digest, kind):
        begin(digest, kind)
        gateway.cancel.set()

    monkeypatch.setattr(gateway.journal, "begin", cancel_after_journal)
    calls = []
    gateway.runner = lambda *args: calls.append(args)
    with pytest.raises(ValueError):
        gateway._upload(snapshot, permit, "video")
    assert calls == []
    SqliteUploadJournal.recover_interrupted(app.state.store)
    gateway.cancel.clear()
    assert gateway.journal.get(snapshot.digest, "video") is None


def test_distinct_historical_attempts_each_consume_conservative_budget(
    local_setup, publishing_context, monkeypatch
):
    from uuid import uuid4

    from fabu.publishing.coordinator import PublicationCoordinator

    policy, app, snapshot, gateway = prepared_job(local_setup, publishing_context, monkeypatch)
    store, assets, presets, approvals, *_ = publishing_context
    coordinator = PublicationCoordinator(
        store, assets, presets, approvals, policy.authority, None, local_policy=policy
    )
    run = coordinator.register(snapshot, "live")
    now = policy.clock().isoformat()
    with store.transaction() as db:
        for _ in range(3):
            id = str(uuid4())
            db.execute(
                "INSERT INTO target_attempts VALUES (?,?,?,?,?,NULL,'FAILED',?,?)",
                (
                    id,
                    snapshot.job_id,
                    1,
                    snapshot.targets[0].account.account_id,
                    snapshot.digest,
                    now,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO publication_attempt_links VALUES (?,?,0,NULL,1)", (id, run["id"])
            )
    assert policy.remaining_today() == 0
    with pytest.raises(ValueError, match="local_daily_quota_exhausted"):
        policy.reserve(snapshot)
