import threading
from datetime import UTC, datetime

import test_gateway as gateway_fixtures
import test_local_policy as policy_fixtures
from test_api_security import login
from test_live_coordinator import transport

gateway_setup = gateway_fixtures.gateway_setup
local_setup = policy_fixtures.local_setup


def test_default_link_title_enters_real_queue_only_for_new_authorized_job(
    local_setup, publishing_context, monkeypatch
):
    from fabu.api.app import create_app
    from fabu.worker import Worker

    policy, original_app, _, gateway = local_setup
    store, assets, presets, approvals, old, output, config = publishing_context
    app = create_app(original_app.state.settings, store)
    app.state.assets, app.state.presets, app.state.approvals = assets, presets, approvals
    app.state.credentials = gateway.credentials
    gateway.authority = app.state.publication_authority
    policy = app.state.live_policy
    policy.enable()
    with store.transaction() as db:
        for item in config.accounts:
            db.execute(
                "INSERT INTO publication_catalog VALUES ('live',?,?,?,?)",
                (
                    item.account.account_id,
                    item.account.model_dump_json(),
                    item.category.model_dump_json() if item.category else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
    client, headers = login(app)
    response = client.put(
        "/api/publishing/defaults",
        headers=headers,
        json={
            "scope": "live",
            "accounts": [
                {
                    "account_id": x.account.account_id,
                    "declaration": x.declaration.model_dump(mode="json"),
                }
                for x in config.accounts
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["profile"]["execution"] == "live"
    response = client.post(
        "/api/workflow/jobs",
        headers=headers,
        json={"source_url": old.spec.source_url, "title": old.spec.title},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["spec"]["preset_version_id"] == old.spec.preset_version_id
    assert policy.is_authorized(created["id"]) and not policy.is_authorized(old.id)
    with store.transaction() as db:
        oldrow = db.execute(
            "SELECT source_json,plan_digest,output_json FROM jobs WHERE id=?", (old.id,)
        ).fetchone()
        db.execute(
            "UPDATE jobs SET state='READY',source_json=?,plan_digest=?,output_json=? WHERE id=?",
            (*oldrow, created["id"]),
        )
        db.execute(
            "INSERT INTO processing_snapshots SELECT ?,content_revision,preset_version_id,"
            "source_json,plan_json,created_at FROM processing_snapshots WHERE job_id=?",
            (created["id"], old.id),
        )
    monkeypatch.setattr(approvals, "approval_matches", lambda *args: False)

    def factory(snapshot, permit, cancel):
        from fabu.publishing.journal import SqliteUploadJournal

        transport(gateway, snapshot)
        gateway.journal = SqliteUploadJournal(store, snapshot, scope="live")
        gateway.cancel = cancel
        gateway.bind_user_job(policy, permit, snapshot)
        return gateway

    app.state.publication_queue.live_factory = factory
    with Worker(store) as worker:
        assert app.state.publication_profiles.process_ready(worker, threading.Event())
        assert app.state.publication_queue.process_next(
            worker, threading.Event(), lambda **kw: None
        )
        assert not app.state.publication_queue.process_next(
            worker, threading.Event(), lambda **kw: None
        )
    result = client.get(f"/api/jobs/{created['id']}/targets?scope=live").json()
    assert [t["state"] for t in result["targets"]] == ["PROVIDER_ACCEPTED"] * 3, result
    assert result["reason"] == "provider_tracking_pending" and not result["can_submit"]
    assert store.get(old.id).state == "READY"
    assert client.get("/api/publishing/readiness").json()["enabled"] is True


def test_workflow_remembers_selected_default_preset_across_restart(local_setup, publishing_context):
    from fabu.api.app import create_app
    from fabu.publishing.defaults import get_default_preset

    policy, previous, _, gateway = local_setup
    store, assets, presets, approvals, old, *_ = publishing_context
    app = create_app(previous.state.settings, store)
    app.state.assets, app.state.presets, app.state.approvals = assets, presets, approvals
    client, headers = login(app)
    preset = presets.save_draft(None, {"name": "chosen", "steps": [], "output": {}})
    response = client.put(
        "/api/publishing/default-preset", headers=headers, json={"preset_version_id": preset.id}
    )
    assert response.status_code == 200
    assert get_default_preset(app).id == preset.id
    restarted = create_app(previous.state.settings, store)
    assert get_default_preset(restarted).id == preset.id
    assert client.get("/api/settings").json()["default_preset_version_id"] == preset.id
