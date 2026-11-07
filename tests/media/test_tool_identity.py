"""Tool identity must invalidate preview compatibility without executing replaced binaries."""

import pytest


def test_media_tool_identity_changes_after_binary_replacement(tmp_path):
    from fabu.media.runtime import MediaTools

    a = tmp_path / "ffmpeg.exe"
    b = tmp_path / "ffprobe.exe"
    a.write_bytes(b"first-build")
    b.write_bytes(b"probe-build")
    tools = MediaTools(ffmpeg_path=a, ffprobe_path=b)
    first = tools.identity()
    a.write_bytes(b"second-build")
    assert tools.identity() != first


def test_bound_plan_cannot_render_with_different_tools(tmp_path):
    from threading import Event

    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import bind_tool_identity, compile_plan
    from fabu.media.models import InputBundle, PlanInvalid
    from fabu.media.renderer import render
    from fabu.media.runtime import MediaTools

    p = preset()
    plan = compile_plan(InputBundle(source=info_fixture(tmp_path)), p)
    plan = bind_tool_identity(plan, p, {"ffmpeg": "a" * 64, "ffprobe": "b" * 64})
    a = tmp_path / "ffmpeg.exe"
    b = tmp_path / "ffprobe.exe"
    a.write_bytes(b"different")
    b.write_bytes(b"different")
    tools = MediaTools(ffmpeg_path=a, ffprobe_path=b)
    with pytest.raises(PlanInvalid, match="tool"):
        render(plan, tmp_path / "out", Event(), tools=tools)


@pytest.mark.media
def test_real_preview_approval_is_bound_to_tool_fingerprint(context, monkeypatch):
    from fabu.media.models import compatibility_version
    from fabu.media.preview import build_preview
    from fabu.media.service import ProcessingPaused

    service, store, assets, approvals, preset, job, calls = context
    with pytest.raises(ProcessingPaused):
        service.process_job(store.claim_next("test-worker"))
    plan = service.get_plan(job.id)
    assert plan.tool_identity == service.tools.identity()
    manifest = build_preview(
        plan, [(0, 500)], workspace=service.workspace.parent / "preview", tools=service.tools
    )
    approvals.record_preview(manifest, preset.id)
    approvals.approve_preview(manifest.id)
    assert approvals.approval_matches(preset, plan.asset_refs, compatibility_version(plan))
    changed = plan.model_copy(update={"tool_identity": {"ffmpeg": "f" * 64, "ffprobe": "e" * 64}})
    assert not approvals.approval_matches(preset, plan.asset_refs, compatibility_version(changed))


@pytest.mark.media
def test_current_tool_change_invalidates_same_saved_approval(context):
    from fabu.media.models import compatibility_version
    from fabu.media.preview import build_preview
    from fabu.media.service import ProcessingPaused

    service, store, assets, approvals, preset, job, _ = context
    with pytest.raises(ProcessingPaused):
        service.process_job(store.claim_next("test-worker"))
    plan = service.get_plan(job.id)
    approvals.current_tools = service.tools.identity
    preview = build_preview(
        plan, [(0, 500)], workspace=service.workspace.parent / "preview", tools=service.tools
    )
    approvals.record_preview(preview, preset.id)
    approvals.approve_preview(preview.id)
    assert approvals.approval_matches(preset, plan.asset_refs, compatibility_version(plan))
    approvals.current_tools = lambda: {"ffmpeg": "f" * 64, "ffprobe": "e" * 64}
    assert not approvals.approval_matches(preset, plan.asset_refs, compatibility_version(plan))


def test_render_boundary_rehashes_even_when_mtime_and_size_are_preserved(tmp_path):
    import os
    from types import SimpleNamespace

    from fabu.media.models import PlanInvalid
    from fabu.media.runtime import MediaTools

    a = tmp_path / "ffmpeg.exe"
    b = tmp_path / "ffprobe.exe"
    a.write_bytes(b"first")
    b.write_bytes(b"probe")
    tools = MediaTools(ffmpeg_path=a, ffprobe_path=b)
    plan = SimpleNamespace(tool_identity=tools.identity())
    stat = a.stat()
    a.write_bytes(b"other")
    os.utime(a, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(PlanInvalid, match="tool"):
        tools.validate_plan_tools(plan)
