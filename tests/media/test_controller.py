import time
from threading import Event, get_ident

import pytest


def wait_for(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Condition did not become true")


@pytest.mark.media
def test_controller_serializes_jobs_and_previews(context, monkeypatch):
    from fabu.media.controller import MediaController
    from fabu.media.preview import build_preview

    service, store, _, approvals, preset, job, _ = context
    timeline = []
    original = service.process_job

    def process(*args, **kwargs):
        timeline.append(("job-start", get_ident()))
        try:
            return original(*args, **kwargs)
        finally:
            timeline.append(("job-end", get_ident()))

    def preview(*args, **kwargs):
        timeline.append(("preview-start", get_ident()))
        try:
            return build_preview(*args, **kwargs)
        finally:
            timeline.append(("preview-end", get_ident()))

    monkeypatch.setattr(service, "process_job", process)
    monkeypatch.setattr("fabu.media.controller.build_preview", preview)
    controller = MediaController(
        store, lambda cancel: service, service.workspace.parent / "previews"
    )
    controller.start()
    try:
        wait_for(lambda: store.get(job.id).state == "PAUSED")
        paused = store.get(job.id)
        operation = controller.enqueue_preview(paused, [(0, 500)])
        with pytest.raises(ValueError):
            controller.enqueue_preview(paused, [(0, 500)])

        def result():
            with store.connection() as db:
                return db.execute(
                    "SELECT state FROM media_operations WHERE id=?", (operation["id"],)
                ).fetchone()[0]

        wait_for(lambda: result() == "SUCCEEDED")
        assert len({thread for _, thread in timeline}) == 1
        assert [event for event, _ in timeline] == [
            "job-start",
            "job-end",
            "preview-start",
            "preview-end",
        ]
        assert not controller.job_busy(job.id)
        assert controller.status()["running"]
    finally:
        controller.stop()
    assert not controller.status()["running"]


@pytest.mark.media
def test_second_controller_cannot_steal_global_worker(context):
    from fabu.media.controller import MediaController

    service, store, _, _, _, _, _ = context
    first = MediaController(store, lambda cancel: service, service.workspace.parent / "previews")
    second = MediaController(store, lambda cancel: service, service.workspace.parent / "previews")
    first.start()
    try:
        second.start()
        assert not second.status()["running"]
        assert second.status()["error_code"] == "worker_already_running"
        assert first.status()["running"]
    finally:
        second.stop()
        first.stop()


@pytest.mark.media
def test_cancel_marks_job_before_stopping_owned_work(context):
    from fabu.media.controller import MediaController
    from fabu.media.source import SourceUnavailable

    service, store, _, _, _, job, _ = context
    entered = Event()

    def factory(cancel):
        def source(_):
            entered.set()
            cancel.wait(10)
            raise SourceUnavailable("stopped")

        service.source_loader = source
        return service

    controller = MediaController(store, factory, service.workspace.parent / "previews")
    controller.start()
    try:
        assert entered.wait(5)
        active = store.get(job.id)
        store.transition(job.id, active.revision, "CANCELED", "user_canceled")
        controller.cancel_job(job.id)
        wait_for(lambda: controller.status()["job_id"] is None)
        assert store.get(job.id).state == "CANCELED"
    finally:
        controller.stop()


def test_cancel_does_not_use_a_stale_public_status_snapshot(tmp_path, monkeypatch):
    from fabu.media.controller import MediaController
    from fabu.store import JobStore

    controller = MediaController(
        JobStore(tmp_path / "db.sqlite3"), lambda cancel: None, tmp_path / "preview"
    )
    controller._set(job_id="next-job")
    monkeypatch.setattr(controller, "status", lambda: {"job_id": "finished-job"})
    controller.cancel_job("finished-job")
    assert not controller._cancel.is_set()
    controller.cancel_job("next-job")
    assert controller._cancel.is_set()


@pytest.mark.media
def test_api_preview_to_approved_output_with_real_media(context, media_tools):
    from fastapi.testclient import TestClient

    from fabu.api.app import create_app
    from fabu.settings import Settings

    service, store, _, _, _, job, calls = context
    root = service.workspace.parent
    settings = Settings(state_dir=root, data_dir=root, tools_dir=media_tools.ffmpeg_path.parent)
    app = create_app(settings, store, enable_worker=True, service_factory=lambda cancel: service)
    base = "http://127.0.0.1:8765"
    with TestClient(app, base_url=base) as client:
        session = client.post(
            "/api/session",
            headers={
                "Origin": base,
                "Authorization": f"Bearer {app.state.security.bootstrap_token}",
            },
        )
        headers = {"Origin": base, "X-CSRF-Token": session.json()["csrf_token"]}
        wait_for(lambda: store.get(job.id).state == "PAUSED")
        paused = client.get(f"/api/jobs/{job.id}").json()
        created = client.post(
            "/api/previews",
            headers=headers,
            json={"job_id": job.id, "expected_revision": paused["revision"], "windows": [[0, 500]]},
        )
        assert created.status_code == 202, created.text
        operation = created.json()["id"]

        def result():
            return client.get(f"/api/previews/operations/{operation}").json()

        wait_for(lambda: result()["state"] == "SUCCEEDED")
        preview_id = result()["result"]["preview_id"]
        sample = client.get(f"/api/previews/{preview_id}").json()
        assert sample["approved"] is False and sample["clips"][0]["proxy_label"] == "观看代理"
        clip = client.get(sample["clips"][0]["proxy_url"], headers={"Range": "bytes=0-50"})
        assert clip.status_code == 206 and len(clip.content) == 51
        denied = client.post(
            f"/api/previews/{preview_id}/approve", headers=headers, json={"approved": True}
        )
        assert denied.status_code == 422
        assert (
            client.post(f"/api/previews/{preview_id}/approve", headers=headers, json={}).status_code
            == 200
        )
        resumed = client.post(
            f"/api/jobs/{job.id}/resume",
            headers=headers,
            json={"expected_revision": store.get(job.id).revision},
        )
        assert resumed.status_code == 200, resumed.text
        wait_for(lambda: store.get(job.id).state == "READY")
        output = client.get(f"/api/jobs/{job.id}/output")
        assert output.status_code == 200
        assert "attachment" in output.headers["content-disposition"]
        assert len(output.content) == store.get(job.id).output_manifest["size_bytes"]
        assert len(calls) == 1
        assert client.get("/api/health").json()["publishing_enabled"] is False
