"""Reusable publication configuration and automatic simulation handoff; no real writes."""

import time

import pytest
import test_publish_api
from publishing import conftest as publishing_fixtures
from test_api_security import login

publishing_context = publishing_fixtures.publishing_context
publish_app = test_publish_api.publish_app


def profile(client, headers, context, scope="simulation"):
    configured = client.put(
        f"/api/jobs/{context[4].id}/publication",
        json=test_publish_api.draft(context, scope=scope),
        headers=headers,
    )
    assert configured.status_code == 200
    response = client.post(
        "/api/publish/profiles",
        json={
            "job_id": context[4].id,
            "expected_revision": configured.json()["revision"],
            "name": "日常助眠",
            "scope": scope,
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()


def workflow(context, profile_id, **changes):
    return {
        "source_url": "https://youtu.be/Z6imSiJLd9Q",
        "title": "剃光头",
        "preset_version_id": context[4].spec.preset_version_id,
        "profile_id": profile_id,
        "mode": "immediate",
        **changes,
    }


def ready_clone(context, new_job):
    import json

    from fabu.media.service import load_job_plan

    store = context[0]
    old = store.get(context[4].id)
    source = {**old.source_manifest, "video_id": "Z6imSiJLd9Q"}
    plan = load_job_plan(store, old.id)
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET state='READY',source_json=?,plan_digest=?,output_json=? WHERE id=?",
            (json.dumps(source), old.plan_digest, json.dumps(old.output_manifest), new_job["id"]),
        )
        db.execute(
            "INSERT INTO processing_snapshots VALUES (?,?,?,?,?,?)",
            (
                new_job["id"],
                new_job["content_revision"],
                new_job["spec"]["preset_version_id"],
                json.dumps(source),
                plan.model_dump_json(),
                "2026-11-07T00:00:00+00:00",
            ),
        )


def test_saved_profile_copies_metadata_not_source_title_or_time(publish_app, publishing_context):
    client, headers = login(publish_app)
    p = profile(client, headers, publishing_context)
    assert p["scope"] == "simulation" and len(p["accounts"]) == 3
    response = client.post(
        "/api/workflow/jobs", json=workflow(publishing_context, p["id"]), headers=headers
    )
    assert response.status_code == 201
    job = response.json()
    assert job["spec"]["title"] == "剃光头"
    assert job["spec"]["publication_profile_id"] == p["id"]
    assert job["spec"]["target_ids"] == ["sim-douyin", "sim-toutiao", "sim-bilibili"]
    assert client.get("/api/publish/profiles").json()[0]["id"] == p["id"]
    assert (
        client.put(f"/api/publish/profiles/{p['id']}", json={}, headers=headers).status_code == 404
    )


def test_ready_media_automatically_flows_to_simulation_once(publish_app, publishing_context):
    client, headers = login(publish_app)
    p = profile(client, headers, publishing_context)
    created = client.post(
        "/api/workflow/jobs", json=workflow(publishing_context, p["id"]), headers=headers
    ).json()
    ready_clone(publishing_context, created)
    controller = publish_app.state.media_controller
    controller.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = client.get(f"/api/jobs/{created['id']}/targets").json()
            if status["state"] == "COMPLETE":
                break
            time.sleep(0.03)
        assert [t["state"] for t in status["targets"]] == ["SUBMITTED"] * 3
        assert status["topic"]["group_name"] == "2aZjVP8u"
        count = len(publishing_context[0].attempts(created["id"]))
        controller.stop()
        controller.start()
        assert len(publishing_context[0].attempts(created["id"])) == count == 3
    finally:
        controller.stop()


def test_profile_autochain_cannot_bypass_media_approval(
    publish_app, publishing_context, monkeypatch
):
    client, headers = login(publish_app)
    p = profile(client, headers, publishing_context)
    created = client.post(
        "/api/workflow/jobs", json=workflow(publishing_context, p["id"]), headers=headers
    ).json()
    ready_clone(publishing_context, created)
    monkeypatch.setattr(publishing_context[3], "approval_matches", lambda *args: False)
    controller = publish_app.state.media_controller
    controller.start()
    try:
        deadline = time.monotonic() + 5
        while (
            time.monotonic() < deadline
            and publishing_context[0].get(created["id"]).state == "READY"
        ):
            time.sleep(0.03)
        assert publishing_context[0].get(created["id"]).state == "PAUSED"
        assert not publishing_context[0].attempts(created["id"])
    finally:
        controller.stop()


def test_unknown_profile_and_user_injected_target_or_permission_is_rejected(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    assert (
        client.post(
            "/api/workflow/jobs", json=workflow(publishing_context, "missing"), headers=headers
        ).status_code
        == 409
    )
    p = profile(client, headers, publishing_context)
    for extra in [{"live": True}, {"approved": True}, {"target_ids": ["outside"]}]:
        assert (
            client.post(
                "/api/workflow/jobs",
                json=workflow(publishing_context, p["id"], **extra),
                headers=headers,
            ).status_code
            == 422
        )


def test_real_profile_cannot_create_an_automatically_publishing_job(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    p = profile(client, headers, publishing_context)
    with publishing_context[0].transaction() as db:
        db.execute("UPDATE publication_profiles SET scope='live' WHERE id=?", (p["id"],))
    result = client.post(
        "/api/workflow/jobs", json=workflow(publishing_context, p["id"]), headers=headers
    )
    assert result.status_code == 409
    assert not publishing_context[0].attempts(publishing_context[4].id)


def test_profile_cancel_and_restart_do_not_start_handoff(publish_app, publishing_context):
    client, headers = login(publish_app)
    p = profile(client, headers, publishing_context)
    created = client.post(
        "/api/workflow/jobs", json=workflow(publishing_context, p["id"]), headers=headers
    ).json()
    ready_clone(publishing_context, created)
    store = publishing_context[0]
    job = store.get(created["id"])
    store.transition(job.id, job.revision, "CANCELED", "user_canceled")
    controller = publish_app.state.media_controller
    controller.start()
    try:
        assert not store.attempts(job.id)
        assert client.get(f"/api/jobs/{job.id}/targets").json()["state"] == "NOT_PREPARED"
    finally:
        controller.stop()


def test_expired_workflow_never_changes_to_immediate(publish_app, publishing_context):
    from datetime import UTC, datetime, timedelta

    client, headers = login(publish_app)
    p = profile(client, headers, publishing_context)
    response = client.post(
        "/api/workflow/jobs",
        json=workflow(
            publishing_context,
            p["id"],
            mode="native_schedule",
            scheduled_at_utc=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        ),
        headers=headers,
    )
    assert response.status_code == 409


def interrupted_handoff(publish_app, context, client, headers):
    from datetime import UTC, datetime

    p = profile(client, headers, context)
    created = client.post(
        "/api/workflow/jobs", json=workflow(context, p["id"]), headers=headers
    ).json()
    ready_clone(context, created)
    with context[0].transaction() as db:
        db.execute(
            "INSERT INTO workflow_handoffs VALUES (?,?,'PREPARING',NULL,?)",
            (created["id"], created["content_revision"], datetime.now(UTC).isoformat()),
        )
    publish_app.state.publication_profiles.recover_interrupted()
    return context[0].get(created["id"])


def test_interrupted_handoff_can_resume_same_content_once_with_explicit_request(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    job = interrupted_handoff(publish_app, publishing_context, client, headers)
    state = client.get(f"/api/workflow/jobs/{job.id}/handoff").json()
    assert state["can_resume"] and state["state"] == "PAUSED"
    resumed = client.post(
        f"/api/workflow/jobs/{job.id}/resume",
        json={"expected_revision": job.revision},
        headers=headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["content_revision"] == job.content_revision
    assert resumed.json()["state"] == "READY"
    assert (
        client.post(
            f"/api/workflow/jobs/{job.id}/resume",
            json={"expected_revision": job.revision},
            headers=headers,
        ).status_code
        == 409
    )
    controller = publish_app.state.media_controller
    controller.start()
    try:
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            if client.get(f"/api/jobs/{job.id}/targets").json()["state"] == "COMPLETE":
                break
            time.sleep(0.03)
        assert len(publishing_context[0].attempts(job.id)) == 3
    finally:
        controller.stop()


def test_handoff_resume_cannot_bypass_uncertain_external_effects(publish_app, publishing_context):
    from datetime import UTC, datetime

    client, headers = login(publish_app)
    job = interrupted_handoff(publish_app, publishing_context, client, headers)
    with publishing_context[0].transaction() as db:
        db.execute(
            "INSERT INTO upload_attempts VALUES ('unknown-upload','simulation','video',"
            "?,?,'Z6imSiJLd9Q','UNKNOWN',NULL,?,?)",
            ("a" * 64, "b" * 64, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
    assert not client.get(f"/api/workflow/jobs/{job.id}/handoff").json()["can_resume"]
    assert (
        client.post(
            f"/api/workflow/jobs/{job.id}/resume",
            json={"expected_revision": job.revision},
            headers=headers,
        ).status_code
        == 409
    )
    assert publishing_context[0].get(job.id).state == "PAUSED"


def test_canceled_handoff_is_not_revived(publish_app, publishing_context):
    client, headers = login(publish_app)
    job = interrupted_handoff(publish_app, publishing_context, client, headers)
    canceled = publishing_context[0].transition(job.id, job.revision, "CANCELED", "user_canceled")
    assert not client.get(f"/api/workflow/jobs/{job.id}/handoff").json()["can_resume"]
    assert (
        client.post(
            f"/api/workflow/jobs/{job.id}/resume",
            json={"expected_revision": canceled.revision},
            headers=headers,
        ).status_code
        == 409
    )


@pytest.mark.parametrize("restriction", ["expired", "inactive_account", "attempt", "frozen"])
def test_interrupted_handoff_rechecks_requirements_without_requeue(
    publish_app, publishing_context, restriction
):
    import json

    from fabu.domain import TargetAttempt

    client, headers = login(publish_app)
    job = interrupted_handoff(publish_app, publishing_context, client, headers)
    store = publishing_context[0]
    with store.transaction() as db:
        if restriction == "expired":
            spec = job.spec.model_dump(mode="json")
            spec.update(publish_mode="native_schedule", scheduled_at_utc="2020-01-01T00:00:00Z")
            db.execute("UPDATE jobs SET spec_json=? WHERE id=?", (json.dumps(spec), job.id))
        elif restriction == "inactive_account":
            # Simulation catalog is generated, so alter the immutable profile to reference
            # an unavailable stable ID rather than adding a second test-only catalog.
            row = db.execute(
                "SELECT config_json FROM publication_profiles WHERE id=?",
                (job.spec.publication_profile_id,),
            ).fetchone()
            config = json.loads(row[0])
            config["accounts"][0]["account"]["account_id"] = "unavailable-account"
            db.execute(
                "UPDATE publication_profiles SET config_json=? WHERE id=?",
                (json.dumps(config), job.spec.publication_profile_id),
            )
        elif restriction == "frozen":
            db.execute("UPDATE jobs SET content_frozen=1 WHERE id=?", (job.id,))
    if restriction == "attempt":
        store.record_attempt(
            TargetAttempt(
                job_id=job.id,
                revision=job.content_revision,
                target_id="sim-douyin",
                payload_digest="a" * 64,
                state="FAILED",
            )
        )
    response = client.post(
        f"/api/workflow/jobs/{job.id}/resume",
        json={"expected_revision": job.revision},
        headers=headers,
    )
    assert response.status_code == 409
    assert store.get(job.id).state == "PAUSED"
    with store.connection() as db:
        assert not db.execute(
            "SELECT 1 FROM publication_commands WHERE job_id=?", (job.id,)
        ).fetchone()
