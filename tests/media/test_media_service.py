from datetime import UTC, datetime

import pytest
from PIL import Image


@pytest.fixture
def context(tmp_path, media_tools):
    from test_composition import make_clip

    from fabu.assets import AssetStore
    from fabu.domain import JobSpec
    from fabu.media.approvals import PreviewApprovalStore
    from fabu.media.service import MediaService
    from fabu.media.source import SelectedFormat, SourceManifest
    from fabu.presets import PresetStore
    from fabu.store import JobStore

    store = JobStore(tmp_path / "state.sqlite3")
    assets = AssetStore(tmp_path / "assets", store)
    presets = PresetStore(store, assets)
    preset = presets.save_draft(None, {"name": "baseline", "steps": [], "output": {}})
    source = assets.import_file(
        make_clip(tmp_path / "source.mp4", media_tools, duration="0.5"), "video"
    )
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (320, 180), "red").save(cover)
    thumb = assets.import_file(cover, "image")
    manifest = SourceManifest(
        video_id="L6imSiJLd9Q",
        source=source,
        thumbnail=thumb,
        selected_format=SelectedFormat(
            selector="test",
            video_format_id="test",
            audio_format_id=None,
            width=320,
            height=180,
            fps=30,
        ),
        fetched_at=datetime.now(UTC),
    )
    calls = []

    def load(job):
        calls.append(job.id)
        return manifest

    approvals = PreviewApprovalStore(store, presets, assets)
    service = MediaService(store, presets, assets, approvals, media_tools, tmp_path / "jobs", load)
    job = store.create(
        JobSpec(
            source_url="https://youtu.be/L6imSiJLd9Q", title="剪发", preset_version_id=preset.id
        )
    )
    return service, store, presets, approvals, preset, job, calls


@pytest.mark.media
def test_unapproved_output_cannot_enter_publish(context):
    from fabu.media.service import ProcessingPaused

    service, store, _, _, _, job, _ = context
    claimed = store.claim_next("test-worker")
    with pytest.raises(ProcessingPaused, match="preview_required"):
        service.process_job(claimed)
    paused = store.get(job.id)
    assert paused.state == "PAUSED" and paused.pause_reason == "preview_required"
    assert service.get_plan(job.id).preset_digest
    with pytest.raises(ValueError):
        service.require_approved_output(paused)


@pytest.mark.media
def test_approved_processing_reuses_source_and_freezes_version(context, media_tools, tmp_path):
    from fabu.media.preview import build_preview
    from fabu.media.service import ProcessingPaused

    service, store, presets, approvals, preset, job, calls = context
    with pytest.raises(ProcessingPaused):
        service.process_job(store.claim_next("test-worker"))
    preview = build_preview(
        service.get_plan(job.id), [(0, 500)], workspace=tmp_path / "previews", tools=media_tools
    )
    approvals.record_preview(preview, preset.id)
    approvals.approve_preview(preview.id)
    presets.save_draft(
        preset.preset_id, {"name": "new global draft", "steps": [], "output": {"crf": 19}}
    )
    paused = store.get(job.id)
    store.transition(job.id, paused.revision, "NEW", None)
    manifest = service.process_job(store.claim_next("test-worker"))
    ready = store.get(job.id)
    assert ready.state == "READY" and ready.spec.preset_version_id == preset.id
    assert ready.owner_worker_id is None
    assert manifest.plan_digest == service.get_plan(job.id).digest
    assert service.require_approved_output(ready) == manifest
    assert len(calls) == 1
    manifest.path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        service.require_approved_output(ready)


@pytest.mark.media
def test_new_source_out_of_range_pauses(context):
    service, store, presets, _, preset, job, _ = context
    changed = presets.save_draft(
        preset.preset_id,
        {
            "name": "trim",
            "steps": [
                {
                    "id": "trim",
                    "kind": "trim",
                    "enabled": True,
                    "scope": "main",
                    "params": {"start_ms": 0, "end_ms": 3000},
                    "asset_ids": [],
                }
            ],
            "output": {},
        },
    )
    store.revise(
        job.id, job.revision, job.spec.model_copy(update={"preset_version_id": changed.id})
    )
    with pytest.raises(ValueError):
        service.process_job(store.claim_next("test-worker"))
    paused = store.get(job.id)
    assert paused.state == "PAUSED" and paused.pause_reason == "pipeline_invalid"
    assert paused.output_manifest is None


@pytest.mark.media
def test_verified_output_cache_reused_on_resume(context, media_tools, tmp_path, monkeypatch):
    from fabu.media.preview import build_preview
    from fabu.media.service import ProcessingPaused

    service, store, _, approvals, preset, job, calls = context
    with pytest.raises(ProcessingPaused):
        service.process_job(store.claim_next("test-worker"))
    preview = build_preview(
        service.get_plan(job.id), [(0, 500)], workspace=tmp_path / "previews", tools=media_tools
    )
    approvals.record_preview(preview, preset.id)
    approvals.approve_preview(preview.id)
    paused = store.get(job.id)
    store.transition(job.id, paused.revision, "NEW", None)
    first = service.process_job(store.claim_next("test-worker"))
    ready = store.get(job.id)
    paused = store.transition(job.id, ready.revision, "PAUSED", "manual")
    store.transition(job.id, paused.revision, "NEW", None)

    def no_render(*args, **kwargs):
        raise AssertionError("verified cache should not be encoded again")

    monkeypatch.setattr("fabu.media.service.render", no_render)
    second = service.process_job(store.claim_next("test-worker"))
    assert second == first and len(calls) == 1
