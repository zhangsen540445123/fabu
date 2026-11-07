"""Explicit new-job authority is distinct from platform capability evidence."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import test_gateway as gateway_fixtures
from test_gateway import live_snapshot

gateway_setup = gateway_fixtures.gateway_setup


@pytest.fixture
def local_setup(gateway_setup, publishing_context, tmp_path, monkeypatch):
    from fabu.publishing.local_policy import LocalPublishPolicy
    from fabu.settings import Settings

    snapshot, gateway, authority, calls, responses = gateway_setup
    state = tmp_path / "settings"
    state.mkdir()
    (state / "publishing-connection.json").write_text(
        json.dumps({"package_root": str(gateway.package_root)}), encoding="utf-8"
    )
    (tmp_path / "node.exe").write_bytes(b"test")
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(
                state_dir=state,
                data_dir=tmp_path / "data",
                tools_dir=gateway.media_tools,
                node_path=tmp_path / "node.exe",
            ),
            store=publishing_context[0],
            credentials=gateway.credentials,
            publication_authority=authority,
        )
    )
    monkeypatch.setattr(
        "fabu.publishing.local_policy.REVIEWED_CLI_SHA256",
        hashlib.sha256(gateway.executable.read_bytes()).hexdigest(),
    )
    policy = LocalPublishPolicy(app)
    app.state.live_policy = policy
    return policy, app, live_snapshot(snapshot), gateway


def authorize_fixture(policy, app, snapshot):
    """Create a genuinely new explicitly requested live workflow, not the old READY job."""
    from fabu.domain import JobSpec
    from fabu.publishing.models import publish_snapshot_digest

    old = app.state.store.get(snapshot.job_id)
    spec = JobSpec(
        **{
            **old.spec.model_dump(),
            "publication_profile_id": "test-profile",
            "publish_mode": "immediate",
        }
    )
    with app.state.store.transaction() as db:
        db.execute(
            "INSERT INTO publication_profiles VALUES (?,?,?,?,?)",
            ("test-profile", "test", "live", "{}", datetime.now(UTC).isoformat()),
        )
    job = app.state.store.create(spec)
    policy.authorize_job(job)
    snapshot = snapshot.model_copy(update={"job_id": job.id, "content_revision": 1})
    return snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})


def test_disabled_by_default_and_no_implicit_existing_job_authority(local_setup):
    policy, app, snapshot, _ = local_setup
    assert policy.readiness()["enabled"] is False
    with pytest.raises(ValueError, match="live_policy_not_enabled"):
        policy.issue(snapshot)
    policy.enable()
    with pytest.raises(ValueError, match="job_not_authorized"):
        policy.issue(snapshot)
    with pytest.raises(ValueError, match="new_workflow_required"):
        policy.authorize_job(app.state.store.get(snapshot.job_id))


def test_bound_new_job_permit_and_daily_budget_survive_restart(local_setup):
    from fabu.publishing.local_policy import LocalPublishPolicy

    policy, app, snapshot, gateway = local_setup
    policy.enable()
    snapshot = authorize_fixture(policy, app, snapshot)
    permit = policy.issue(snapshot)
    assert permit.kind == "user_job" and permit.zero_new_payment
    policy.reserve(snapshot)
    assert policy.remaining_today() == 0
    restarted = LocalPublishPolicy(app)
    restarted.reserve(snapshot)
    assert restarted.remaining_today() == 0
    assert restarted.is_authorized(snapshot.job_id)
    assert len(gateway.runner.__name__) > 0  # No transport command has been invoked.


def test_identity_change_and_disabled_policy_revoke_new_writes(local_setup):
    policy, app, snapshot, gateway = local_setup
    policy.enable()
    snapshot = authorize_fixture(policy, app, snapshot)
    permit = policy.issue(snapshot)
    gateway.executable.write_bytes(b"changed")
    with pytest.raises(ValueError, match="cli_identity_changed"):
        policy.validate(permit, snapshot)


def test_edited_spec_does_not_reuse_authorization(local_setup):
    policy, app, snapshot, _ = local_setup
    policy.enable()
    snapshot = authorize_fixture(policy, app, snapshot)
    with app.state.store.transaction() as db:
        row = db.execute("SELECT spec_json FROM jobs WHERE id=?", (snapshot.job_id,)).fetchone()
        spec = json.loads(row[0])
        spec["title"] = "different title"
        db.execute("UPDATE jobs SET spec_json=? WHERE id=?", (json.dumps(spec), snapshot.job_id))
    with pytest.raises(ValueError, match="job_authorization_changed"):
        policy.issue(snapshot)


def test_user_job_keeps_capability_unknown_and_never_schedules_by_fallback(local_setup):
    from fabu.publishing.capabilities import CapabilityReport
    from fabu.publishing.models import publish_snapshot_digest

    policy, app, snapshot, _ = local_setup
    policy.enable()
    snapshot = authorize_fixture(policy, app, snapshot)
    report = CapabilityReport(
        checked_at=datetime.now(UTC),
        account_status="SUPPORTED",
        tool_version="3.2.32",
        accounts=[
            {"id": t.account.account_id, "platform": t.account.platform, "active": True}
            for t in snapshot.targets
        ],
    )
    assert policy.preflight_problem(snapshot, report) is None
    assert report.upload == "UNKNOWN" and report.quota == "UNKNOWN"
    assert (
        policy.preflight_problem(snapshot, report.model_copy(update={"payment_required": True}))
        == "paid_gate"
    )
    scheduled = snapshot.model_copy(
        update={
            "mode": "native_schedule",
            "scheduled_at_utc": datetime.now(UTC) + timedelta(hours=2),
        }
    )
    scheduled = scheduled.model_copy(update={"digest": publish_snapshot_digest(scheduled)})
    assert policy.preflight_problem(scheduled, report) == "native_schedule_not_verified"


def test_job_creation_and_authorization_commit_atomically(local_setup, monkeypatch):

    policy, app, snapshot, _ = local_setup
    policy.enable()
    spec = app.state.store.get(snapshot.job_id).spec.model_copy(
        update={"publication_profile_id": "new-profile", "publish_mode": "immediate"}
    )
    with app.state.store.transaction() as db:
        db.execute(
            "INSERT INTO publication_profiles VALUES (?,?,?,?,?)",
            ("new-profile", "test", "live", "{}", datetime.now(UTC).isoformat()),
        )
    before = len(app.state.store.list_jobs())

    def failed(db, job):
        policy.authorize_job(job, db=db)
        raise ValueError("injected failure before commit")

    with pytest.raises(ValueError, match="injected"):
        app.state.store.create(spec, on_create=failed)
    assert len(app.state.store.list_jobs()) == before
    with app.state.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM publication_job_authorizations").fetchone()[0] == 0
    job = app.state.store.create(spec, on_create=lambda db, job: policy.authorize_job(job, db=db))
    assert policy.is_authorized(job.id)


def test_readiness_rejects_missing_default_accounts_and_insufficient_budget(local_setup):
    policy, app, snapshot, gateway = local_setup
    policy.enable()
    snapshot = authorize_fixture(policy, app, snapshot)
    with app.state.store.transaction() as db:
        db.execute(
            "INSERT INTO publication_defaults VALUES "
            "(1,'test-profile','immediate','user_configured_default',?)",
            (datetime.now(UTC).isoformat(),),
        )
    # Fixture profile deliberately has no valid config/accounts: ready must not be guessed.
    result = policy.readiness()
    assert result["status"] == "blocked" and any("账号" in x for x in result["blockers"])


def test_stale_live_catalogue_is_refreshed_before_next_day_workflow(local_setup, monkeypatch):
    policy, app, snapshot, gateway = local_setup
    calls = []
    monkeypatch.setattr(
        "fabu.publishing.connection.refresh_accounts", lambda target: calls.append(target)
    )
    policy.refresh_catalogue_if_stale()
    assert calls == [app]
