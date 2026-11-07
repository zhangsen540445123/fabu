"""Explicit defaults are local intent, never an external publishing permit."""

import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import test_publish_api
from publishing import conftest as publishing_fixtures
from test_api_security import login
from test_workflow import ready_clone, workflow

publishing_context = publishing_fixtures.publishing_context
publish_app = test_publish_api.publish_app


def live_defaults(app):
    from fabu.publishing.service import catalogue

    accounts = []
    with app.state.store.transaction() as db:
        for account, category in catalogue(app.state.store, "simulation"):
            account = account.model_copy(
                update={"account_id": account.account_id.replace("sim-", "real-")}
            )
            if category:
                category = category.model_copy(update={"account_id": account.account_id})
            db.execute(
                "INSERT INTO publication_catalog VALUES (?,?,?,?,?)",
                (
                    "live",
                    account.account_id,
                    account.model_dump_json(),
                    category.model_dump_json() if category else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            accounts.append(
                {
                    "account_id": account.account_id,
                    "declaration": {
                        "origin": "self_created",
                        "confirmed": True,
                        "statement": "self_shot" if account.platform == "toutiao" else "none",
                        "bilibili_type": "self_produced"
                        if account.platform == "bilibili"
                        else None,
                    },
                }
            )
    return {"scope": "live", "mode": "immediate", "topic_override": None, "accounts": accounts}


def save_defaults(app, client, headers):
    body = live_defaults(app)
    response = client.put("/api/publishing/defaults", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json(), body


def test_explicit_defaults_are_persistent_and_leave_existing_job_unchanged(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    old = publishing_context[0].get(publishing_context[4].id)
    assert client.get("/api/publishing/defaults").json()["profile"] is None
    defaults, body = save_defaults(publish_app, client, headers)
    assert defaults["profile"]["scope"] == "live"
    assert defaults["profile"]["execution"] == "preflight_only"
    assert defaults["profile"]["can_run"] is True
    assert defaults["mode"] == "immediate"
    assert defaults["declaration_source"] == "user_configured_default"
    assert client.get("/api/publishing/defaults").json() == defaults
    assert publishing_context[0].get(old.id) == old
    form = client.get(f"/api/jobs/{old.id}/publication").json()
    assert form["source"] == "default" and form["scope"] == "live"
    assert form["accounts"] == body["accounts"]
    assert form["topic_override"] is None
    assert form["topic_selection"]["status"] == "selected"
    assert not publishing_context[0].attempts(old.id)


@pytest.mark.parametrize("change", ["unconfirmed", "missing_account", "duplicate", "schedule"])
def test_invalid_default_rejected_without_creating_profile(publish_app, change):
    client, headers = login(publish_app)
    body = live_defaults(publish_app)
    if change == "unconfirmed":
        body["accounts"][0]["declaration"]["confirmed"] = False
    elif change == "missing_account":
        body["accounts"][0]["account_id"] = "outside"
    elif change == "duplicate":
        body["accounts"][1] = body["accounts"][0]
    else:
        body["mode"] = "native_schedule"
    assert client.put("/api/publishing/defaults", json=body, headers=headers).status_code in {
        409,
        422,
    }
    assert client.get("/api/publishing/defaults").json()["profile"] is None


def test_saved_draft_precedes_defaults_and_preserves_licensed_reuse(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    job = publishing_context[4]
    saved = test_publish_api.draft(publishing_context)
    assert (
        client.put(f"/api/jobs/{job.id}/publication", json=saved, headers=headers).status_code
        == 200
    )
    save_defaults(publish_app, client, headers)
    form = client.get(f"/api/jobs/{job.id}/publication").json()
    assert form["source"] == "draft" and form["scope"] == "simulation"
    assert form["accounts"] == saved["accounts"]
    assert all(a["declaration"]["origin"] == "licensed_reuse" for a in form["accounts"])


def test_live_ready_handoff_checks_media_once_without_approval_or_external_effects(
    publish_app, publishing_context, monkeypatch
):
    client, headers = login(publish_app)
    defaults, body = save_defaults(publish_app, client, headers)
    created = client.post(
        "/api/workflow/jobs",
        json=workflow(publishing_context, defaults["profile"]["id"]),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    job = created.json()
    ready_clone(publishing_context, job)
    monkeypatch.setattr(publishing_context[3], "approval_matches", lambda *args: False)
    store = publishing_context[0]
    worker = SimpleNamespace(active=True, store=store)
    profiles = publish_app.state.publication_profiles
    assert profiles.process_ready(worker, threading.Event()) is True
    assert profiles.process_ready(worker, threading.Event()) is False
    status = client.get(f"/api/workflow/jobs/{job['id']}/handoff").json()
    assert status["state"] == "PAUSED" and status["error_code"] == "live_publish_not_enabled"
    assert status["can_resume"] is False
    assert store.get(job["id"]).state == "READY"
    form = client.get(f"/api/jobs/{job['id']}/publication").json()
    assert form["source"] == "profile" and form["accounts"] == body["accounts"]
    assert form["topic_selection"]["group_name"] == "2aZjVP8u"
    with store.connection() as db:
        for table in [
            "target_attempts",
            "publication_commands",
            "publication_runs",
            "upload_attempts",
        ]:
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert (
        client.post(
            f"/api/jobs/{job['id']}/submit",
            json={"scope": "live", "snapshot_digest": "a" * 64},
            headers=headers,
        ).status_code
        == 409
    )


def test_default_profile_is_immutable_for_existing_workflow(publish_app, publishing_context):
    client, headers = login(publish_app)
    defaults, body = save_defaults(publish_app, client, headers)
    created = client.post(
        "/api/workflow/jobs",
        json=workflow(publishing_context, defaults["profile"]["id"]),
        headers=headers,
    ).json()
    body["accounts"] = body["accounts"][:1]
    newer = client.put("/api/publishing/defaults", json=body, headers=headers).json()
    assert newer["profile"]["id"] != defaults["profile"]["id"]
    form = client.get(f"/api/jobs/{created['id']}/publication").json()
    assert len(form["accounts"]) == 3


def test_live_handoff_rejects_corrupted_output(publish_app, publishing_context):
    client, headers = login(publish_app)
    defaults, _ = save_defaults(publish_app, client, headers)
    created = client.post(
        "/api/workflow/jobs",
        json=workflow(publishing_context, defaults["profile"]["id"]),
        headers=headers,
    ).json()
    ready_clone(publishing_context, created)
    store = publishing_context[0]
    output = dict(store.get(created["id"]).output_manifest)
    output["sha256"] = "0" * 64
    with store.transaction() as db:
        db.execute("UPDATE jobs SET output_json=? WHERE id=?", (json.dumps(output), created["id"]))
    assert publish_app.state.publication_profiles.process_ready(
        SimpleNamespace(active=True, store=store), threading.Event()
    )
    status = client.get(f"/api/workflow/jobs/{created['id']}/handoff").json()
    assert status["error_code"] == "workflow_preflight_failed"
    assert not store.attempts(created["id"])


def test_manual_draft_detaches_old_profile_to_preserve_changed_declaration(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    defaults, body = save_defaults(publish_app, client, headers)
    created = client.post(
        "/api/workflow/jobs",
        json=workflow(publishing_context, defaults["profile"]["id"]),
        headers=headers,
    ).json()
    ready_clone(publishing_context, created)
    body["accounts"] = [
        {
            "account_id": "real-bilibili",
            "declaration": {
                "origin": "licensed_reuse",
                "confirmed": True,
                "statement": "none",
                "bilibili_type": "repost",
            },
        }
    ]
    saved = client.put(
        f"/api/jobs/{created['id']}/publication",
        json={**body, "expected_revision": created["revision"]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["spec"]["publication_profile_id"] is None
    form = client.get(f"/api/jobs/{created['id']}/publication").json()
    assert form["source"] == "draft" and form["accounts"] == body["accounts"]
    assert not publish_app.state.publication_profiles.process_ready(
        SimpleNamespace(active=True, store=publishing_context[0]), threading.Event()
    )


def test_switching_scope_does_not_replace_saved_declaration_with_defaults(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    job = publishing_context[4]
    assert (
        client.put(
            f"/api/jobs/{job.id}/publication",
            json=test_publish_api.draft(publishing_context),
            headers=headers,
        ).status_code
        == 200
    )
    save_defaults(publish_app, client, headers)
    form = client.get(f"/api/jobs/{job.id}/publication?scope=live").json()
    assert form["source"] == "fallback" and form["accounts"] == []


@pytest.mark.parametrize("title", ["未知标题", "剃光头和剪发"])
def test_live_default_requires_unambiguous_title_or_explicit_topic(
    publish_app, publishing_context, title
):
    client, headers = login(publish_app)
    defaults, _ = save_defaults(publish_app, client, headers)
    assert (
        client.post(
            "/api/workflow/jobs",
            json=workflow(publishing_context, defaults["profile"]["id"], title=title),
            headers=headers,
        ).status_code
        == 409
    )


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_live_preflight_retains_output_and_can_resume_without_side_effects(
    publish_app, publishing_context, interrupted
):
    client, headers = login(publish_app)
    defaults, _ = save_defaults(publish_app, client, headers)
    created = client.post(
        "/api/workflow/jobs",
        json=workflow(publishing_context, defaults["profile"]["id"]),
        headers=headers,
    ).json()
    ready_clone(publishing_context, created)
    store, profiles = publishing_context[0], publish_app.state.publication_profiles
    worker = SimpleNamespace(active=True, store=store)
    if interrupted:
        with store.transaction() as db:
            db.execute(
                "INSERT INTO workflow_handoffs VALUES (?,?,'PREPARING',NULL,?)",
                (created["id"], 1, datetime.now(UTC).isoformat()),
            )
        profiles.recover_interrupted()
    else:
        with store.transaction() as db:
            original = db.execute(
                "SELECT account_json FROM publication_catalog WHERE account_id='real-douyin'"
            ).fetchone()[0]
            disabled = json.loads(original)
            disabled["active"] = False
            db.execute(
                "UPDATE publication_catalog SET account_json=? WHERE account_id='real-douyin'",
                (json.dumps(disabled),),
            )
        assert profiles.process_ready(worker, threading.Event())
        with store.transaction() as db:
            db.execute(
                "UPDATE publication_catalog SET account_json=? WHERE account_id='real-douyin'",
                (original,),
            )
    # Publishing fixtures keep synthetic output at the temporary root, not under media/.
    publish_app.state.settings = publish_app.state.settings.model_copy(
        update={"data_dir": store.path.parent}
    )
    current = store.get(created["id"])
    assert current.state == "READY"
    assert (
        client.get(f"/api/jobs/{current.id}/output", headers={"Range": "bytes=0-15"}).status_code
        == 206
    )
    assert client.get(f"/api/workflow/jobs/{current.id}/handoff").json()["can_resume"] is True
    # Use the controller only for its real wake function; don't start a media worker in this test.
    resumed = client.post(
        f"/api/workflow/jobs/{current.id}/resume",
        json={"expected_revision": current.revision},
        headers=headers,
    )
    assert resumed.status_code == 200, resumed.text
    assert profiles.process_ready(worker, threading.Event())
    assert (
        client.get(f"/api/workflow/jobs/{current.id}/handoff").json()["error_code"]
        == "live_publish_not_enabled"
    )
    assert not store.attempts(current.id)
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM publication_commands").fetchone()[0] == 0
