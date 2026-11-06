import threading
import time

import pytest
from test_api_security import login
from test_publish_api import prepare_api


def test_disk_full_then_restart_no_partial_publish(publish_app, publishing_context, monkeypatch):
    from fabu.publishing.journal import SqliteUploadJournal
    from fabu.worker import Worker

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    queue = publish_app.state.publication_queue
    original = SqliteUploadJournal.complete

    def full(*args):
        raise OSError(28, "Synthetic disk full after resource transfer")

    monkeypatch.setattr(SqliteUploadJournal, "complete", full)
    queue.enqueue(queue.get_run(run["snapshot_digest"], "simulation"), "submit")
    with Worker(publishing_context[0]) as worker:
        queue.process_next(worker, threading.Event(), lambda **state: None)
    assert publishing_context[0].get(run["job_id"]).state == "PAUSED"
    assert not publishing_context[0].attempts(run["job_id"])
    monkeypatch.setattr(SqliteUploadJournal, "complete", original)
    controller = publish_app.state.media_controller
    controller.start()
    try:
        with publishing_context[0].connection() as db:
            rows = db.execute("SELECT state FROM upload_attempts").fetchall()
        assert [r[0] for r in rows] == ["UNKNOWN"]
        result = client.post(
            f"/api/jobs/{run['job_id']}/submit",
            json={"snapshot_digest": run["snapshot_digest"]},
            headers=headers,
        )
        assert result.status_code == 409
        assert not publishing_context[0].attempts(run["job_id"])
    finally:
        controller.stop()


def test_shutdown_during_submit_reconciles_without_resend(publish_app, publishing_context):
    from fabu.publishing.fake_gateway import FakePublisher

    started = threading.Event()
    submissions = []

    class Delayed(FakePublisher):
        def submit(self, prepared, permit, target):
            submissions.append(target)
            started.set()
            return super().submit(prepared, permit, target)

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    queue = publish_app.state.publication_queue
    queue.publisher_factory = lambda authority, cancel: Delayed(
        authority, delay_seconds=10, cancel=cancel
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
        assert started.wait(5)
        controller.stop()
        assert not controller.status()["running"]
        controller.start()
        status = client.get(f"/api/jobs/{run['job_id']}/targets").json()
        assert [t["state"] for t in status["targets"]] == ["UNKNOWN", "PENDING", "PENDING"]
        assert status["can_reconcile"] and not status["can_submit"]
        assert (
            client.post(
                f"/api/jobs/{run['job_id']}/submit",
                json={"snapshot_digest": run["snapshot_digest"]},
                headers=headers,
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/jobs/{run['job_id']}/reconcile",
                json={"snapshot_digest": run["snapshot_digest"]},
                headers=headers,
            ).status_code
            == 202
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = client.get(f"/api/jobs/{run['job_id']}/targets").json()
            if status["command"]["state"] == "COMPLETE":
                break
            time.sleep(0.02)
        assert status["targets"][0]["state"] == "UNKNOWN"
        assert submissions == [run["targets"][0]["account_id"]]
    finally:
        controller.stop()


def test_duplicate_source_claim_is_visible_before_enqueue(publish_app, publishing_context):
    from fabu.publishing.models import PublishSnapshot
    from fabu.publishing.service import public_run

    client, headers = login(publish_app)
    run = prepare_api(client, headers, publishing_context)
    queue = publish_app.state.publication_queue
    original = queue.get_run(run["snapshot_digest"], "simulation")
    snapshot = PublishSnapshot.model_validate_json(original["snapshot_json"])
    from uuid import uuid4

    other_id = str(uuid4())
    # Same source/account was reserved by a distinct frozen publication snapshot.
    with publishing_context[0].transaction() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(publication_runs)")]
        other = {**original, "id": other_id, "snapshot_digest": "f" * 64}
        db.execute(
            "INSERT INTO publication_runs ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(other[k] for k in columns),
        )
        db.execute(
            "INSERT INTO publication_source_claims VALUES (?,?,?,?)",
            ("simulation", snapshot.video_id, snapshot.targets[0].account.account_id, other_id),
        )
    result = public_run(publishing_context[0], original)
    assert result["source_conflict"] and not result["can_submit"]
    with pytest.raises(ValueError):
        queue.enqueue(original, "submit")
