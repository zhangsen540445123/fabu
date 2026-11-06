import io
from fractions import Fraction
from threading import Event

import pytest
from PIL import Image


def test_window_plan_uses_final_timeline_not_source_seek(tmp_path):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle, verify_plan_digest
    from fabu.media.preview import PreviewWindow, window_plan

    plan = compile_plan(InputBundle(source=info_fixture(tmp_path)), preset())
    clip = window_plan(plan, PreviewWindow(start_frame=60, end_frame=90))
    assert clip.source == plan.source
    assert clip.expected_duration_ms == 1000
    assert clip.audio_profile.expected_duration == 1
    assert "trim=start_frame=60:end_frame=90" in clip.video_graph
    assert "atrim=start=2:end=3" in ",".join(clip.stages[-1].audio_filters)
    verify_plan_digest(clip)


def test_preview_window_outside_timeline_rejected(tmp_path):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.preview import normalize_windows

    plan = compile_plan(InputBundle(source=info_fixture(tmp_path)), preset())
    for windows in [[(0, 11000)], [(500, 100)], [(-1, 1000)], []]:
        with pytest.raises(ValueError):
            normalize_windows(plan, windows)
    result = normalize_windows(plan, [(100, 433)])
    assert result[0].start_frame == 3 and result[0].end_frame == 13


@pytest.mark.media
def test_preview_is_same_plan_window(tmp_path, media_tools):
    from test_compiler import preset
    from test_composition import make_clip, picture, step

    from fabu.assets import AssetStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.preview import build_preview, verify_preview
    from fabu.media.probe import probe_media
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output
    from fabu.store import JobStore

    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "state.sqlite3"))
    source = probe_media(make_clip(tmp_path / "main.mp4", media_tools), media_tools.probe)
    intro = picture(tmp_path, assets, "intro", "red")
    layer = picture(tmp_path, assets, "main_layer", "blue", (10, 10))
    p = preset(
        [
            step("image_layer", {"start_ms": 200, "end_ms": 600, "width": 0.1}, [layer.id]),
            step("intro", {"duration_ms": 500}, [intro.id]),
        ]
    )
    plan = compile_plan(InputBundle(source=source, assets={intro.id: intro, layer.id: layer}), p)
    complete = validate_output(
        render(plan, tmp_path / "full", Event(), tools=media_tools), plan, tools=media_tools
    )
    preview = build_preview(plan, [(300, 1200)], workspace=tmp_path / "previews", tools=media_tools)
    verify_preview(preview)
    assert preview.plan_digest == plan.digest
    assert preview.clips[0].official.media_info.codec == "hevc"
    assert preview.clips[0].proxy.label == "观看代理"
    assert preview.clips[0].proxy.codec == "h264"

    def pixels(path, time):
        result = media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-ss",
                str(time),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=64:36",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-",
            ]
        )
        with Image.open(io.BytesIO(result.stdout)) as image:
            return image.convert("RGB").getpixel((2, 2))

    for local_time in [0.1, 0.3, 0.5, 0.8]:
        expected = pixels(complete.path, local_time + 0.3)
        actual = pixels(preview.clips[0].official.path, local_time)
        assert max(abs(a - b) for a, b in zip(expected, actual, strict=True)) < 12
    cached = build_preview(plan, [(300, 1200)], workspace=tmp_path / "previews", tools=media_tools)
    assert cached.id == preview.id
    preview.clips[0].official.path.write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        verify_preview(preview)


def test_default_windows_cover_layer_and_segment_boundaries(tmp_path):
    from test_baseline import info_fixture
    from test_compiler import preset
    from test_composition import picture, step

    from fabu.assets import AssetStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.preview import default_windows
    from fabu.store import JobStore

    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))
    source = info_fixture(tmp_path).model_copy(update={"duration": Fraction(50)})
    intro = picture(tmp_path, assets, "intro", "red")
    layer = picture(tmp_path, assets, "layer", "blue", (10, 10))
    steps = [
        step("image_layer", {"start_ms": 2000, "end_ms": 3000}, [layer.id]),
        step("intro", {"duration_ms": 2000}, [intro.id]),
    ]
    plan = compile_plan(
        InputBundle(source=source, assets={intro.id: intro, layer.id: layer}), preset(steps)
    )
    windows = default_windows(plan)
    for point in [0, 2000, 4000, 5000, 52000]:
        assert any(start <= point <= end for start, end in windows), (point, windows)


def test_preview_end_on_fractional_frame_not_extended(tmp_path):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.preview import normalize_windows

    source = info_fixture(tmp_path).model_copy(update={"duration": Fraction(2, 3)})
    plan = compile_plan(InputBundle(source=source), preset())
    window = normalize_windows(plan, [(0, 667)])[0]
    assert window.end_frame == 20


@pytest.fixture
def rendered_preview(tmp_path, media_tools):
    from test_composition import make_clip

    from fabu.assets import AssetStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.preview import build_preview
    from fabu.media.probe import probe_media
    from fabu.presets import PresetStore
    from fabu.store import JobStore

    store = JobStore(tmp_path / "state.sqlite3")
    assets = AssetStore(tmp_path / "assets", store)
    presets = PresetStore(store, assets)
    preset = presets.save_draft(None, {"name": "baseline", "steps": [], "output": {}})
    source = probe_media(
        make_clip(tmp_path / "short.mp4", media_tools, duration="0.5"), media_tools.probe
    )
    plan = compile_plan(InputBundle(source=source), preset)
    preview = build_preview(plan, [(0, 500)], workspace=tmp_path / "previews", tools=media_tools)
    return store, assets, presets, preset, preview


@pytest.mark.media
def test_new_valid_source_reuses_approval(rendered_preview, tmp_path):
    from test_baseline import info_fixture

    from fabu.media.approvals import PreviewApprovalStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import ENGINE_VERSION, InputBundle

    store, assets, presets, preset, manifest = rendered_preview
    approvals = PreviewApprovalStore(store, presets, assets)
    approvals.record_preview(manifest, preset.id)
    assert not approvals.approval_matches(preset, {}, ENGINE_VERSION)
    approval = approvals.approve_preview(manifest.id)
    assert approval.preview_id == manifest.id
    other = compile_plan(InputBundle(source=info_fixture(tmp_path)), preset)
    assert other.source_hash != manifest.plan.source_hash
    assert other.preset_digest == manifest.plan.preset_digest
    assert approvals.approval_matches(preset, {}, ENGINE_VERSION)


@pytest.mark.media
def test_changed_output_or_engine_invalidates_approval(rendered_preview):
    from fabu.media.approvals import PreviewApprovalStore
    from fabu.media.models import ENGINE_VERSION

    store, assets, presets, preset, manifest = rendered_preview
    approvals = PreviewApprovalStore(store, presets, assets)
    approvals.record_preview(manifest, preset.id)
    approvals.approve_preview(manifest.id)
    changed = presets.save_draft(
        preset.preset_id, {"name": "modified", "steps": [], "output": {"crf": 19}}
    )
    assert not approvals.approval_matches(changed, {}, ENGINE_VERSION)
    assert not approvals.approval_matches(preset, {}, "incompatible-engine")
    assert approvals.approve_preview(manifest.id).preview_id == manifest.id


@pytest.mark.media
def test_expired_or_missing_sample_cannot_be_approved(rendered_preview):
    from datetime import timedelta

    from fabu.media.approvals import PreviewApprovalStore

    store, assets, presets, preset, manifest = rendered_preview
    approvals = PreviewApprovalStore(store, presets, assets)
    approvals.record_preview(manifest, preset.id)
    with pytest.raises(ValueError, match="expired"):
        approvals.approve_preview(manifest.id, now=manifest.created_at + timedelta(days=8))
    manifest.clips[0].official.path.unlink()
    with pytest.raises(ValueError):
        approvals.approve_preview(manifest.id)


@pytest.mark.media
def test_expired_preview_cache_can_generate_fresh_sample(rendered_preview, tmp_path, media_tools):
    from datetime import timedelta

    from fabu.media.preview import _manifest_digest, build_preview

    _, _, _, _, manifest = rendered_preview
    old = manifest.model_copy(update={"created_at": manifest.created_at - timedelta(days=8)})
    old = old.model_copy(update={"digest": _manifest_digest(old)})
    saved = tmp_path / "previews" / manifest.cache_key / "manifest.json"
    saved.write_text(old.model_dump_json(), encoding="utf-8")
    fresh = build_preview(
        manifest.plan, [(0, 500)], workspace=tmp_path / "previews", tools=media_tools
    )
    assert fresh.id != old.id


@pytest.mark.media
def test_changed_asset_or_output_invalidates_approval(rendered_preview, tmp_path, media_tools):
    import os
    import stat

    from test_composition import picture, step

    from fabu.media.approvals import PreviewApprovalStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import ENGINE_VERSION, InputBundle
    from fabu.media.preview import build_preview

    store, assets, presets, preset, manifest = rendered_preview
    image = picture(tmp_path, assets, "layer", "red", (10, 10))
    preset = presets.save_draft(
        preset.preset_id,
        {
            "name": "layer",
            "steps": [step("image_layer", {}, [image.id]).model_dump()],
            "output": {},
        },
    )
    refs = {image.id: image}
    plan = compile_plan(InputBundle(source=manifest.plan.source, assets=refs), preset)
    sample = build_preview(plan, [(0, 500)], workspace=tmp_path / "previews", tools=media_tools)
    approvals = PreviewApprovalStore(store, presets, assets)
    approvals.record_preview(sample, preset.id)
    approvals.approve_preview(sample.id)
    assert approvals.approval_matches(preset, refs, ENGINE_VERSION)
    os.chmod(image.local_path, stat.S_IWRITE | stat.S_IREAD)
    image.local_path.write_bytes(b"different")
    assert not approvals.approval_matches(preset, refs, ENGINE_VERSION)


def test_output_validation_honors_cancel_before_tools(tmp_path, media_tools):
    from test_baseline import info_fixture

    from fabu.media.baseline import build_baseline_plan
    from fabu.media.validator import validate_output

    cancel = Event()
    cancel.set()
    with pytest.raises(ValueError, match="canceled"):
        validate_output(
            tmp_path / "missing.mp4",
            build_baseline_plan(info_fixture(tmp_path)),
            tools=media_tools,
            cancel=cancel,
        )
