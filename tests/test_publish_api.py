"""HTTP publishing gates use a synthetic catalogue and never a real transport."""

from datetime import UTC, datetime, timedelta

import pytest
from publishing import conftest as publishing_fixtures
from test_api_security import login

publishing_context = publishing_fixtures.publishing_context


@pytest.fixture
def publish_app(publishing_context):
    from fabu.api.app import create_app
    from fabu.settings import Settings

    store, assets, presets, approvals, *_ = publishing_context
    app = create_app(
        Settings(
            state_dir=store.path.parent,
            data_dir=store.path.parent / "media",
            tools_dir=store.path.parent / "tools",
        ),
        store,
    )
    app.state.assets, app.state.presets, app.state.approvals = assets, presets, approvals
    return app


def draft(publishing_context, **changes):
    job, config = publishing_context[4], publishing_context[6]
    return {
        "expected_revision": job.revision,
        "scope": "simulation",
        "mode": "immediate",
        "accounts": [
            {
                "account_id": "sim-" + a.account.platform,
                "declaration": a.declaration.model_dump(mode="json"),
            }
            for a in config.accounts
        ],
        "topic_override": None,
        **changes,
    }


def test_account_catalog_separates_simulation_and_real_inventory(publish_app):
    client, _ = login(publish_app)
    response = client.get("/api/accounts?scope=simulation")
    assert response.status_code == 200
    assert response.json()["scope"] == "simulation"
    rows = response.json()["accounts"]
    assert {r["account_id"] for r in rows} == {"sim-douyin", "sim-toutiao", "sim-bilibili"}
    assert client.get("/api/accounts?scope=live").json()["accounts"] == []


def test_denied_mode_cannot_be_posted_directly(publish_app, publishing_context):
    client, headers = login(publish_app)
    job_id = publishing_context[4].id
    response = client.put(
        f"/api/jobs/{job_id}/publication",
        json=draft(publishing_context, scope="live"),
        headers=headers,
    )
    assert response.status_code == 409
    response = client.post(
        f"/api/jobs/{job_id}/submit",
        json={"snapshot_digest": "a" * 64, "scope": "live"},
        headers=headers,
    )
    assert response.status_code == 409
    assert publishing_context[0].attempts(job_id) == []


def test_metadata_configuration_keeps_verified_media_and_clones_plan(
    publish_app, publishing_context
):
    from fabu.media.service import load_job_plan

    client, headers = login(publish_app)
    old = publishing_context[4]
    plan = load_job_plan(publishing_context[0], old.id)
    response = client.put(
        f"/api/jobs/{old.id}/publication", json=draft(publishing_context), headers=headers
    )
    assert response.status_code == 200
    new = publishing_context[0].get(old.id)
    assert new.state == "READY" and new.content_revision == old.content_revision + 1
    assert new.output_manifest == old.output_manifest and new.source_manifest == old.source_manifest
    assert load_job_plan(publishing_context[0], old.id) == plan
    assert new.spec.target_ids == ["sim-douyin", "sim-toutiao", "sim-bilibili"]
    assert (
        client.put(
            f"/api/jobs/{old.id}/publication", json=draft(publishing_context), headers=headers
        ).status_code
        == 409
    )


def test_snapshot_is_server_built_and_response_has_no_private_paths(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    job_id = publishing_context[4].id
    configured = client.put(
        f"/api/jobs/{job_id}/publication", json=draft(publishing_context), headers=headers
    )
    assert configured.status_code == 200
    revision = configured.json()["revision"]
    response = client.post(
        "/api/publish/preflight",
        json={"job_id": job_id, "expected_revision": revision, "scope": "simulation"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["can_submit"] is True and body["scope"] == "simulation"
    assert body["topic"]["group_name"] == "QKHLZMFO"
    assert len(body["targets"]) == 3
    assert "snapshot_json" not in response.text and "local_path" not in response.text
    assert str(publishing_context[0].path.parent) not in response.text
    assert (
        client.post(
            "/api/publish/preflight",
            json={
                "job_id": job_id,
                "expected_revision": revision,
                "scope": "simulation",
                "approved": True,
            },
            headers=headers,
        ).status_code
        == 422
    )


def test_client_cannot_supply_category_evidence_or_override_media_approval(
    publish_app, publishing_context, monkeypatch
):
    client, headers = login(publish_app)
    job_id = publishing_context[4].id
    command = draft(publishing_context)
    command["accounts"][2]["category"] = {"raw": {"id": 1}}
    assert (
        client.put(f"/api/jobs/{job_id}/publication", json=command, headers=headers).status_code
        == 422
    )
    monkeypatch.setattr(publishing_context[3], "approval_matches", lambda *args: False)
    response = client.put(
        f"/api/jobs/{job_id}/publication", json=draft(publishing_context), headers=headers
    )
    assert response.status_code == 409


def test_native_schedule_remains_utc_and_expired_time_is_rejected(publish_app, publishing_context):
    client, headers = login(publish_app)
    job_id = publishing_context[4].id
    expired = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    command = draft(publishing_context, mode="native_schedule", scheduled_at_utc=expired)
    assert (
        client.put(f"/api/jobs/{job_id}/publication", json=command, headers=headers).status_code
        == 409
    )
    when = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0)
    command["scheduled_at_utc"] = when.isoformat()
    result = client.put(f"/api/jobs/{job_id}/publication", json=command, headers=headers)
    assert result.status_code == 200
    assert publishing_context[0].get(job_id).spec.scheduled_at_utc == when


def test_unmatched_topics_cannot_prepare_without_manual_override(publish_app, publishing_context):
    client, headers = login(publish_app)
    job_id = publishing_context[4].id
    command = draft(publishing_context, topic_override="not-a-known-group")
    assert (
        client.put(f"/api/jobs/{job_id}/publication", json=command, headers=headers).status_code
        == 409
    )


def prepare_api(client, headers, publishing_context):
    job_id = publishing_context[4].id
    job = client.put(
        f"/api/jobs/{job_id}/publication", json=draft(publishing_context), headers=headers
    ).json()
    result = client.post(
        "/api/publish/preflight",
        json={"job_id": job_id, "expected_revision": job["revision"], "scope": "simulation"},
        headers=headers,
    )
    assert result.status_code == 200
    return result.json()


def test_submit_queue_uses_existing_worker_and_persists_partial_results(
    publish_app, publishing_context
):
    from fabu.publishing.fake_gateway import FakePublisher

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    app = publish_app
    app.state.publication_queue.publisher_factory = lambda authority, cancel: FakePublisher(
        authority, outcomes=["SUBMITTED", "FAILED", "SUBMITTED"], cancel=cancel
    )
    app.state.media_controller.start()
    try:
        response = client.post(
            f"/api/jobs/{run['job_id']}/submit",
            json={"snapshot_digest": run["snapshot_digest"]},
            headers=headers,
        )
        assert response.status_code == 202
        import time

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = client.get(f"/api/jobs/{run['job_id']}/targets").json()
            if status["state"] == "PAUSED":
                break
            time.sleep(0.02)
        assert [t["state"] for t in status["targets"]] == ["SUBMITTED", "FAILED", "PENDING"]
        assert status["targets"][1]["can_retry"] is True
        assert status["scope"] == "simulation"
        assert not publishing_context[0].get(run["job_id"]).content_frozen
    finally:
        app.state.media_controller.stop()


def test_unknown_shows_reconcile_not_retry_and_direct_submit_is_blocked(
    publish_app, publishing_context
):
    import threading

    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.worker import Worker

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    queue = publish_app.state.publication_queue
    queue.publisher_factory = lambda authority, cancel: FakePublisher(
        authority, outcomes=["UNKNOWN"], cancel=cancel
    )
    queue.enqueue(queue.get_run(run["snapshot_digest"], "simulation"), "submit")
    with Worker(publishing_context[0]) as worker:
        assert queue.process_next(worker, threading.Event(), lambda **state: None)
    status = client.get(f"/api/jobs/{run['job_id']}/targets").json()
    assert status["can_submit"] is False and status["can_reconcile"] is True
    assert status["targets"][0]["can_retry"] is False
    with pytest.raises(ValueError):
        queue.enqueue(queue.get_run(run["snapshot_digest"], "simulation"), "submit")
    assert len(publishing_context[0].attempts(run["job_id"])) == 1


def test_double_enqueue_and_worker_restart_do_not_duplicate_submission(
    publish_app, publishing_context
):
    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    queue = publish_app.state.publication_queue
    internal = queue.get_run(run["snapshot_digest"], "simulation")
    queue.enqueue(internal, "submit")
    with pytest.raises(ValueError):
        queue.enqueue(internal, "submit")
    assert publish_app.state.media_controller.job_busy(run["job_id"])
    queue.recover_interrupted()
    status = client.get(f"/api/jobs/{run['job_id']}/targets").json()
    assert status["command"]["state"] == "FAILED" and status["can_submit"]
    assert publishing_context[0].attempts(run["job_id"]) == []


def test_canceled_task_does_not_offer_submit_from_old_snapshot(publish_app, publishing_context):
    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    job = publishing_context[0].get(run["job_id"])
    publishing_context[0].transition(job.id, job.revision, "CANCELED", "user_canceled")
    status = client.get(f"/api/jobs/{run['job_id']}/targets").json()
    assert status["can_submit"] is False


@pytest.fixture
def synthetic_cli_package(tmp_path):
    root = tmp_path / "cli-package"
    (root / "bin-native").mkdir(parents=True)
    (root / "bin-native" / "yxer.exe").write_bytes(b"synthetic-never-executed")
    (root / "schemas").mkdir()
    (root / "references").mkdir()
    return root


def test_connection_configuration_never_returns_or_stores_plaintext_key(
    publish_app, synthetic_cli_package
):
    client, headers = login(publish_app)
    secret = "SYNTHETIC_TEST_KEY_DO_NOT_USE"
    response = client.put(
        "/api/publishing/connection",
        json={
            "package_root": str(synthetic_cli_package),
            "api_key": secret,
            "local_client_id": "synthetic-client-id",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["configured"]
    assert secret not in response.text
    assert secret not in client.get("/api/publishing/connection").text
    root = publish_app.state.settings.state_dir
    assert secret.encode() not in (root / "credentials" / "credentials.dpapi").read_bytes()
    assert secret not in (root / "publishing-connection.json").read_text(encoding="utf-8")


def test_readonly_account_refresh_preserves_stable_id_and_does_not_unlock_writes(
    publish_app, synthetic_cli_package
):
    client, headers = login(publish_app)
    connection = {
        "package_root": str(synthetic_cli_package),
        "api_key": "synthetic-only",
        "local_client_id": "synthetic-client",
    }
    assert (
        client.put("/api/publishing/connection", json=connection, headers=headers).status_code
        == 200
    )
    calls = []

    class ReadOnly:
        def query(self, operation):
            calls.append(operation)
            if operation == "version":
                return {"version": "3.2.32"}
            return {
                "accounts": [
                    {"id": "stable-dy", "platform": "douyin", "nickname": "ASMR", "active": True},
                    {
                        "id": "stable-bili",
                        "platform": "bilibili",
                        "nickname": "枕畔慢时光",
                        "active": True,
                    },
                    {
                        "id": "unrequested",
                        "platform": "baijia",
                        "nickname": "other",
                        "active": True,
                    },
                ]
            }

        def categories(self, account_id):
            calls.append("category:" + account_id)
            return {
                "dataList": [
                    {
                        "yixiaoerId": "1031",
                        "yixiaoerName": "生活经验",
                        "raw": {"id": 1031, "name": "生活经验"},
                    }
                ]
            }

    publish_app.state.readonly_cli_factory = lambda *args: ReadOnly()
    result = client.post("/api/accounts/refresh", headers=headers)
    assert result.status_code == 200
    rows = client.get("/api/accounts?scope=live").json()
    assert {r["account_id"] for r in rows["accounts"]} == {"stable-dy", "stable-bili"}
    assert rows["live_enabled"] is False
    assert set(calls) == {"version", "accounts", "category:stable-bili"}
    assert client.get("/api/capabilities").json()["immediate"] == "UNKNOWN"


def test_unknown_publication_cannot_be_restarted_through_media_resume(
    publish_app, publishing_context
):
    import threading

    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.worker import Worker

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    queue = publish_app.state.publication_queue
    queue.publisher_factory = lambda authority, cancel: FakePublisher(
        authority, outcomes=["UNKNOWN"]
    )
    queue.enqueue(queue.get_run(run["snapshot_digest"], "simulation"), "submit")
    with Worker(publishing_context[0]) as worker:
        queue.process_next(worker, threading.Event(), lambda **state: None)
    job = publishing_context[0].get(run["job_id"])
    assert (
        client.post(
            f"/api/jobs/{job.id}/resume", json={"expected_revision": job.revision}, headers=headers
        ).status_code
        == 409
    )
    assert publishing_context[0].get(job.id).state == "PAUSED"


def test_real_account_metadata_can_be_prepared_without_authorizing_submission(
    publish_app, publishing_context
):
    config = publishing_context[6]
    with publishing_context[0].transaction() as db:
        for a in config.accounts:
            db.execute(
                "INSERT INTO publication_catalog VALUES ('live',?,?,?,?)",
                (
                    a.account.account_id,
                    a.account.model_dump_json(),
                    a.category.model_dump_json() if a.category else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
    client, headers = login(publish_app)
    command = draft(publishing_context, scope="live")
    for item, a in zip(command["accounts"], config.accounts, strict=True):
        item["account_id"] = a.account.account_id
    job_id = publishing_context[4].id
    saved = client.put(f"/api/jobs/{job_id}/publication", json=command, headers=headers)
    assert saved.status_code == 200
    result = client.post(
        "/api/publish/preflight",
        json={"job_id": job_id, "expected_revision": saved.json()["revision"], "scope": "live"},
        headers=headers,
    )
    assert result.status_code == 200 and result.json()["scope"] == "live"
    assert result.json()["can_submit"] is False
    assert (
        client.post(
            f"/api/jobs/{job_id}/submit",
            json={"snapshot_digest": result.json()["snapshot_digest"], "scope": "live"},
            headers=headers,
        ).status_code
        == 409
    )
    assert not publishing_context[0].attempts(job_id)


def test_running_simulation_can_cancel_without_restarting_or_submitting_remaining_targets(
    publish_app, publishing_context
):
    import time

    from fabu.publishing.fake_gateway import FakePublisher

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    publish_app.state.publication_queue.publisher_factory = lambda authority, cancel: FakePublisher(
        authority, delay_seconds=3, cancel=cancel
    )
    controller = publish_app.state.media_controller
    controller.start()
    try:
        assert (
            client.post(
                f"/api/jobs/{run['job_id']}/submit",
                json={"snapshot_digest": run["snapshot_digest"]},
                headers=headers,
            ).status_code
            == 202
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = publishing_context[0].get(run["job_id"])
            if job.state == "PUBLISHING" and publishing_context[0].attempts(job.id):
                break
            time.sleep(0.02)
        assert job.state == "PUBLISHING"
        result = client.post(
            f"/api/jobs/{job.id}/cancel", json={"expected_revision": job.revision}, headers=headers
        )
        # Attempt recording may advance CAS after the poll; reload before canceling.
        if result.status_code == 409:
            job = publishing_context[0].get(job.id)
            result = client.post(
                f"/api/jobs/{job.id}/cancel",
                json={"expected_revision": job.revision},
                headers=headers,
            )
        assert result.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and controller.status()["job_id"]:
            time.sleep(0.02)
        assert publishing_context[0].get(job.id).state == "CANCELED"
        assert len(publishing_context[0].attempts(job.id)) == 1
        assert not client.get(f"/api/jobs/{job.id}/targets").json()["can_submit"]
    finally:
        controller.stop()


def test_readonly_category_command_is_literal_and_bounded(tmp_path, monkeypatch):
    import json

    from fabu.diagnostics import ReadOnlyCli
    from fabu.tools import CommandResult

    executable = tmp_path / "yxer.exe"
    executable.write_bytes(b"not-executed")
    config = tmp_path / "config.json"
    config.write_text("{}")
    calls = []

    def runner(spec, cancel):
        calls.append(spec)
        return CommandResult(
            exit_code=0,
            stdout=json.dumps({"ok": True, "data": {"dataList": []}}).encode(),
            stderr=b"",
        )

    monkeypatch.setattr("fabu.diagnostics.run_command", runner)
    reader = ReadOnlyCli(executable, config, tmp_path)
    assert reader.categories("account-id") == {"dataList": []}
    assert calls[0].args == [
        "query",
        "categories",
        "account-id",
        "--type",
        "video",
        "--paths",
        "--json",
    ]
    assert calls[0].timeout_s == 30
    with pytest.raises(ValueError):
        reader.categories("--upload")
    assert len(calls) == 1


def test_submitted_targets_can_refresh_final_state_without_resubmission(
    publish_app, publishing_context
):
    import threading

    from fabu.worker import Worker

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    queue = publish_app.state.publication_queue
    queue.enqueue(queue.get_run(run["snapshot_digest"], "simulation"), "submit")
    with Worker(publishing_context[0]) as worker:
        queue.process_next(worker, threading.Event(), lambda **state: None)
        public = client.get(f"/api/jobs/{run['job_id']}/targets").json()
        assert public["can_reconcile"] and not public["can_submit"]
        publisher = next(iter(queue._publishers.values()))
        before = len([c for c in publisher.calls if c[0] == "submit"])
        publisher.receipts = {
            k: v.model_copy(update={"state": "PUBLISHED"}) for k, v in publisher.receipts.items()
        }
        queue.enqueue(queue.get_run(run["snapshot_digest"], "simulation"), "reconcile")
        queue.process_next(worker, threading.Event(), lambda **state: None)
        final = client.get(f"/api/jobs/{run['job_id']}/targets").json()
        assert [t["state"] for t in final["targets"]] == ["PUBLISHED"] * 3
        assert len([c for c in publisher.calls if c[0] == "submit"]) == before


@pytest.mark.parametrize(
    "version, query_error, expected_calls",
    [
        ("3.2.32", False, ["version", ("details", "existing-provider-id")]),
        ("unsupported", False, ["version"]),
        ("3.2.32", True, ["version", ("details", "existing-provider-id")]),
    ],
)
def test_existing_live_attempt_can_be_queried_without_unlocking_writes(
    publish_app, publishing_context, synthetic_cli_package, version, query_error, expected_calls
):
    import threading

    from fabu.domain import TargetAttempt
    from fabu.publishing.models import PublishSnapshot
    from fabu.worker import Worker

    client, headers = login(publish_app)
    connection = {
        "package_root": str(synthetic_cli_package),
        "api_key": "synthetic-query-only",
        "local_client_id": "synthetic-client",
    }
    assert (
        client.put("/api/publishing/connection", json=connection, headers=headers).status_code
        == 200
    )
    config = publishing_context[6]
    with publishing_context[0].transaction() as db:
        for a in config.accounts:
            db.execute(
                "INSERT INTO publication_catalog VALUES ('live',?,?,?,?)",
                (
                    a.account.account_id,
                    a.account.model_dump_json(),
                    a.category.model_dump_json() if a.category else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
    command = draft(publishing_context, scope="live")
    for item, a in zip(command["accounts"], config.accounts, strict=True):
        item["account_id"] = a.account.account_id
    job_id = publishing_context[4].id
    saved = client.put(f"/api/jobs/{job_id}/publication", json=command, headers=headers).json()
    run = client.post(
        "/api/publish/preflight",
        json={"job_id": job_id, "expected_revision": saved["revision"], "scope": "live"},
        headers=headers,
    ).json()
    queue = publish_app.state.publication_queue
    internal = queue.get_run(run["snapshot_digest"], "live")
    snap = PublishSnapshot.model_validate_json(internal["snapshot_json"])
    attempt = TargetAttempt(
        job_id=job_id,
        revision=snap.content_revision,
        target_id=snap.targets[0].account.account_id,
        payload_digest=snap.digest,
        provider_task_id="existing-provider-id",
        state="UNKNOWN",
    )
    publishing_context[0].record_attempt(attempt)
    with publishing_context[0].transaction() as db:
        db.execute(
            "INSERT INTO publication_attempt_links VALUES (?,?,0,NULL,0)",
            (attempt.id, internal["id"]),
        )
        db.execute("UPDATE publication_runs SET state='PAUSED' WHERE id=?", (internal["id"],))
        db.execute("UPDATE jobs SET state='PAUSED',owner_worker_id=NULL WHERE id=?", (job_id,))
    calls = []

    class ReadOnly:
        def query(self, operation):
            calls.append(operation)
            return {"version": version}

        def details(self, identifier):
            calls.append(("details", identifier))
            if query_error:
                raise RuntimeError("synthetic provider timeout")
            return {"state": "success"}

    publish_app.state.readonly_cli_factory = lambda *args: ReadOnly()
    # Real reconciliation must not construct FakePublisher or a write-capable CliPublisher.
    queue.publisher_factory = lambda *args: (_ for _ in ()).throw(
        AssertionError("Write publisher must not be constructed")
    )
    from unittest.mock import patch

    with patch.object(publish_app.state.media_controller, "status", return_value={"running": True}):
        response = client.post(
            f"/api/jobs/{job_id}/reconcile",
            json={"snapshot_digest": snap.digest, "scope": "live"},
            headers=headers,
        )
        assert response.status_code == 202
    with Worker(publishing_context[0]) as worker:
        queue.process_next(worker, threading.Event(), lambda **state: None)
    assert calls == expected_calls
    assert [a.state for a in publishing_context[0].attempts(job_id)] == ["UNKNOWN"]
    assert not client.get(f"/api/jobs/{job_id}/targets?scope=live").json()["can_submit"]
    assert (
        client.post(
            f"/api/jobs/{job_id}/submit",
            json={"snapshot_digest": snap.digest, "scope": "live"},
            headers=headers,
        ).status_code
        == 409
    )


@pytest.mark.parametrize("provider_id", ["known-id", "task.part:123", "x" * 256])
def test_details_readonly_command_is_literal_and_cannot_invoke_write(
    tmp_path, monkeypatch, provider_id
):
    import json

    from fabu.diagnostics import ReadOnlyCli
    from fabu.tools import CommandResult

    exe = tmp_path / "yxer.exe"
    exe.write_bytes(b"synthetic")
    config = tmp_path / "auth.json"
    config.write_text("{}")
    calls = []

    def run(spec, cancel):
        calls.append(spec.args)
        return CommandResult(
            exit_code=0, stdout=json.dumps({"ok": True, "data": {}}).encode(), stderr=b""
        )

    monkeypatch.setattr("fabu.diagnostics.run_command", run)
    reader = ReadOnlyCli(exe, config, tmp_path)
    assert reader.details(provider_id) == {}
    assert calls == [["query", "details", provider_id, "--json"]]
    with pytest.raises(ValueError):
        reader.details("--publish")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "fault", ["canceled", "timed_out", "output_limit_exceeded", "rejected", "shape"]
)
def test_readonly_details_rejects_incomplete_or_unusable_responses(tmp_path, monkeypatch, fault):
    import json

    from fabu.diagnostics import ReadOnlyCli
    from fabu.tools import CommandResult

    exe = tmp_path / "yxer.exe"
    exe.write_bytes(b"not executed")
    config = tmp_path / "auth.json"
    config.write_text("{}")
    raw = {"ok": fault != "rejected", "data": [] if fault == "shape" else {}}
    flags = {fault: True} if fault in {"canceled", "timed_out", "output_limit_exceeded"} else {}
    monkeypatch.setattr(
        "fabu.diagnostics.run_command",
        lambda spec, cancel: CommandResult(
            exit_code=0, stdout=json.dumps(raw).encode(), stderr=b"", **flags
        ),
    )
    reader = ReadOnlyCli(exe, config, tmp_path)
    with pytest.raises((ValueError, RuntimeError)):
        reader.details("existing-id")


def test_readonly_canceled_before_start_does_not_spawn_process(tmp_path, monkeypatch):
    from fabu.diagnostics import ReadOnlyCli

    exe = tmp_path / "yxer.exe"
    exe.write_bytes(b"not executed")
    config = tmp_path / "auth.json"
    config.write_text("{}")

    def forbidden(*args):
        pytest.fail("canceled read must not launch any subprocess")

    monkeypatch.setattr("fabu.diagnostics.run_command", forbidden)
    reader = ReadOnlyCli(exe, config, tmp_path)
    reader.cancel.set()
    with pytest.raises(RuntimeError):
        reader.details("existing-id")
