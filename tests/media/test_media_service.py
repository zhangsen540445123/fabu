import pytest


@pytest.mark.media
def test_unapproved_output_cannot_enter_publish(context):
    service, store, _, _, _, job, _ = context
    service.process_job(store.claim_next("test-worker"))
    ready = store.get(job.id)
    assert ready.state == "READY" and ready.pause_reason is None
    assert service.get_plan(job.id).preset_digest
    with pytest.raises(ValueError, match="preview approval"):
        service.require_approved_output(ready)


@pytest.mark.media
def test_approved_processing_reuses_source_and_freezes_version(context, media_tools, tmp_path):
    from fabu.media.preview import build_preview

    service, store, presets, approvals, preset, job, calls = context
    service.process_job(store.claim_next("test-worker"))
    preview = build_preview(
        service.get_plan(job.id), [(0, 500)], workspace=tmp_path / "previews", tools=media_tools
    )
    approvals.record_preview(preview, preset.id)
    approvals.approve_preview(preview.id)
    presets.save_draft(
        preset.preset_id, {"name": "new global draft", "steps": [], "output": {"crf": 19}}
    )
    ready = store.get(job.id)
    paused = store.transition(job.id, ready.revision, "PAUSED", "manual")
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
    service, store, _, _, _, job, calls = context
    first = service.process_job(store.claim_next("test-worker"))
    ready = store.get(job.id)
    paused = store.transition(job.id, ready.revision, "PAUSED", "manual")
    store.transition(job.id, paused.revision, "NEW", None)

    def no_render(*args, **kwargs):
        raise AssertionError("verified cache should not be encoded again")

    monkeypatch.setattr("fabu.media.service.render", no_render)
    second = service.process_job(store.claim_next("test-worker"))
    assert second == first and len(calls) == 1


def test_source_login_failure_is_a_safe_persisted_reason(context):
    from fabu.media.service import ProcessingPaused
    from fabu.media.source import _successful
    from fabu.tools import CommandResult

    service, store, _, _, _, job, _ = context

    def fail(_):
        _successful(
            CommandResult(
                exit_code=1,
                stdout=b"",
                stderr=b"Sign in to confirm you are not a bot. token=do-not-persist",
            )
        )

    service.source_loader = fail
    with pytest.raises(ProcessingPaused, match="^source_authentication_required$"):
        service.process_job(store.claim_next("test-worker"))
    paused = store.get(job.id)
    assert paused.state == "PAUSED" and paused.pause_reason == "source_authentication_required"
    assert paused.source_manifest is None and paused.output_manifest is None
    assert "do-not-persist" not in paused.model_dump_json()


def test_untrusted_source_error_text_is_not_used_as_a_pause_reason(context):
    from fabu.media.service import ProcessingPaused
    from fabu.media.source import SourceUnavailable

    service, store, _, _, _, job, _ = context

    def fail(_):
        raise SourceUnavailable("source_authentication_required: token=do-not-persist")

    service.source_loader = fail
    with pytest.raises(ProcessingPaused, match="^source_unavailable$"):
        service.process_job(store.claim_next("test-worker"))
    assert store.get(job.id).pause_reason == "source_unavailable"


def test_browser_session_failure_has_actionable_safe_pause_reason(context):
    from fabu.media.service import ProcessingPaused
    from fabu.media.source import SourceBrowserSessionRequired

    service, store, _, _, _, job, _ = context

    def fail(_):
        raise SourceBrowserSessionRequired("browser_session_required")

    service.source_loader = fail
    with pytest.raises(ProcessingPaused, match="^source_browser_session_required$"):
        service.process_job(store.claim_next("test-worker"))
    paused = store.get(job.id)
    assert paused.pause_reason == "source_browser_session_required"
    assert paused.source_manifest is None and paused.output_manifest is None
