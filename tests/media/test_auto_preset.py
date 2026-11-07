import pytest


@pytest.mark.media
def test_selected_preset_processes_without_creating_preview_approval(context):
    from fabu.media.service import require_verified_job_output

    service, store, presets, approvals, preset, job, calls = context
    result = service.process_job(store.claim_next("test-worker"))
    ready = store.get(job.id)
    assert ready.state == "READY" and ready.pause_reason is None
    assert ready.spec.preset_version_id == preset.id
    assert calls == [job.id]
    assert require_verified_job_output(store, presets, approvals, ready) == result
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM preview_approvals").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM media_previews").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM target_attempts").fetchone()[0] == 0
    with pytest.raises(ValueError, match="preview approval"):
        service.require_approved_output(ready)


@pytest.mark.media
def test_local_output_endpoint_works_before_optional_preview(context, tmp_path, media_tools):
    from test_api_security import login

    from fabu.api.app import create_app
    from fabu.settings import Settings

    service, store, _, _, _, job, _ = context
    result = service.process_job(store.claim_next("test-worker"))
    app = create_app(
        Settings(
            state_dir=tmp_path / "state",
            data_dir=tmp_path,
            tools_dir=media_tools.ffmpeg_path.parent,
        ),
        store,
    )
    client, _ = login(app)
    response = client.get(f"/api/jobs/{job.id}/output")
    assert response.status_code == 200 and response.content == result.path.read_bytes()
    result.path.write_bytes(b"corrupt")
    assert client.get(f"/api/jobs/{job.id}/output").status_code == 409


@pytest.mark.media
def test_unreviewed_output_still_checks_current_tools_and_plan(context):
    from fabu.media.service import require_verified_job_output

    service, store, presets, approvals, _, job, _ = context
    service.process_job(store.claim_next("test-worker"))
    ready = store.get(job.id)
    approvals.current_tools = lambda: {"ffmpeg": "f" * 64, "ffprobe": "e" * 64}
    with pytest.raises(ValueError, match="tool"):
        require_verified_job_output(store, presets, approvals, ready)
    approvals.current_tools = service.tools.identity
    assert require_verified_job_output(store, presets, approvals, ready)


@pytest.mark.media
def test_corrupt_download_still_stops_before_render(context, monkeypatch):
    from fabu.media.service import ProcessingPaused

    service, store, _, _, _, job, _ = context
    real_load = service.source_loader

    def changed(task):
        manifest = real_load(task)
        manifest.source.local_path.write_bytes(b"corrupt downloaded input")
        return manifest

    service.source_loader = changed

    def forbidden(*args, **kwargs):
        raise AssertionError("Corrupt source must not render")

    monkeypatch.setattr("fabu.media.service.render", forbidden)
    with pytest.raises(ProcessingPaused, match="source_unavailable"):
        service.process_job(store.claim_next("test-worker"))
    assert store.get(job.id).output_manifest is None
