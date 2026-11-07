import hashlib
import json

import pytest
from test_baseline import info_fixture

from fabu.media.baseline import build_baseline_plan, build_render_command
from fabu.media.models import OutputProfile, plan_digest, verify_plan_digest


def test_acceleration_profile_and_legacy_payload(tmp_path):
    profile = OutputProfile.model_validate({"acceleration": {"mode": "auto"}})
    assert profile.acceleration.mode == "auto"
    plan = build_baseline_plan(info_fixture(tmp_path))
    payload = plan.model_dump(mode="json", exclude={"digest"})
    payload.pop("execution", None)
    payload["output_profile"].pop("acceleration", None)
    old = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert plan.digest == old
    verify_plan_digest(plan)


def test_unresolved_gpu_plan_cannot_render(tmp_path):
    profile = OutputProfile.model_validate({"acceleration": {"mode": "gpu"}})
    plan = build_baseline_plan(info_fixture(tmp_path)).model_copy(
        update={"output_profile": profile}
    )
    plan = plan.model_copy(update={"digest": plan_digest(plan)})
    with pytest.raises(ValueError, match="resolved"):
        build_render_command(plan, tmp_path / "out.mp4", tmp_path / "p.txt")


def test_nvenc_command_keeps_cpu_filters_but_not_x265_parameters(tmp_path):
    import fabu.media.models as models

    assert hasattr(models, "ExecutionProfile"), "Frozen encoder selection is missing"
    from fabu.media.encoders import bind_execution
    from fabu.media.hardware import HardwareReport

    profile = OutputProfile.model_validate({"acceleration": {"mode": "gpu"}})
    plan = build_baseline_plan(info_fixture(tmp_path)).model_copy(
        update={"output_profile": profile}
    )
    plan = plan.model_copy(update={"digest": plan_digest(plan)})
    report = HardwareReport(
        available=True,
        reason="nvenc_verified",
        fingerprint="a" * 64,
        device_name="test GPU",
        checked_at="2026-11-07T00:00:00+00:00",
    )
    plan = bind_execution(plan, report)
    args = build_render_command(plan, tmp_path / "out.mp4", tmp_path / "p.txt").args
    assert args[args.index("-c:v") + 1] == "hevc_nvenc"
    assert "-crf" not in args and "-x265-params" not in args
    assert "-cq" in args and "-vf" in args
    assert plan.execution.path == "mixed" and plan.execution.device_fingerprint == "a" * 64


def test_auto_fallback_and_strict_gpu_failure(tmp_path):
    assert "acceleration" in OutputProfile.model_fields, "Acceleration intent is missing"
    from fabu.media.encoders import bind_execution
    from fabu.media.hardware import HardwareReport

    report = HardwareReport(
        available=False,
        reason="driver_incompatible",
        fingerprint="a" * 64,
        checked_at="2026-11-07T00:00:00+00:00",
    )
    plan = build_baseline_plan(info_fixture(tmp_path))
    for mode in ("auto", "gpu"):
        p = plan.model_copy(update={"output_profile": OutputProfile(acceleration={"mode": mode})})
        p = p.model_copy(update={"digest": plan_digest(p)})
        if mode == "auto":
            result = bind_execution(p, report)
            assert (
                result.execution.path == "cpu" and result.execution.reason == "driver_incompatible"
            )
        else:
            with pytest.raises(ValueError, match="gpu_driver_incompatible"):
                bind_execution(p, report)


def test_execution_is_part_of_approval_compatibility_and_frozen_retry(tmp_path):
    from types import SimpleNamespace

    from fabu.media.compiler import bind_tool_identity
    from fabu.media.encoders import bind_execution
    from fabu.media.hardware import HardwareReport
    from fabu.media.models import compatibility_version

    profile = OutputProfile(acceleration={"mode": "auto"})
    preset = SimpleNamespace(output=profile.model_dump(mode="json"), steps=[])
    plan = build_baseline_plan(info_fixture(tmp_path)).model_copy(
        update={"output_profile": profile}
    )
    plan = bind_tool_identity(plan, preset, {"ffmpeg": "c" * 64, "ffprobe": "d" * 64})
    failed = HardwareReport(
        available=False,
        reason="driver_incompatible",
        fingerprint="a" * 64,
        checked_at="2026-11-07T00:00:00+00:00",
    )
    good = HardwareReport(
        available=True,
        reason="nvenc_verified",
        fingerprint="b" * 64,
        checked_at="2026-11-07T00:00:00+00:00",
    )
    cpu = bind_execution(plan, failed)
    gpu = bind_execution(plan, good)
    assert compatibility_version(cpu) != compatibility_version(gpu)
    assert bind_execution(plan, good, previous=cpu) == cpu
    with pytest.raises(ValueError, match="gpu_environment_changed"):
        bind_execution(plan, failed, previous=gpu)


@pytest.mark.media
def test_auto_worker_fallback_ready_without_approval_and_records_phases(context):
    from types import SimpleNamespace

    from fabu.domain import JobSpec
    from fabu.media.hardware import HardwareReport

    service, store, presets, approvals, _, _, _ = context
    p = presets.save_draft(
        None, {"name": "auto", "steps": [], "output": {"acceleration": {"mode": "auto"}}}
    )
    j = store.create(
        JobSpec(source_url="https://youtu.be/L6imSiJLd9Q", title="auto", preset_version_id=p.id)
    )
    # Claim the fixture task first; test the newly created auto task independently.
    old = store.claim_next("gpu-test")
    store.transition(old.id, old.revision, "PAUSED", "user_canceled")
    claimed = store.claim_next("gpu-test")
    assert claimed.id == j.id
    service.hardware = SimpleNamespace(
        check=lambda *a: HardwareReport(
            available=False,
            reason="driver_incompatible",
            fingerprint="a" * 64,
            checked_at="2026-11-07T00:00:00+00:00",
        )
    )
    stages = []
    out = service.process_job(claimed, on_stage=stages.append)
    assert store.get(j.id).state == "READY"
    from fabu.media.service import load_job_plan

    plan = load_job_plan(store, j.id)
    assert plan.execution.path == "cpu" and plan.execution.reason == "driver_incompatible"
    approvals.validate_plan_binding(p, plan)
    assert {"input_validation", "hardware_check", "encoding", "output_validation"} <= set(stages)
    assert out.timings["encoding"] > 0 and out.timings["output_validation"] > 0
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM preview_approvals").fetchone()[0] == 0


@pytest.mark.media
def test_strict_gpu_worker_pauses_without_render(context, monkeypatch):
    from types import SimpleNamespace

    from fabu.domain import JobSpec
    from fabu.media.hardware import HardwareReport
    from fabu.media.service import ProcessingPaused

    service, store, presets, _, _, _, _ = context
    p = presets.save_draft(
        None, {"name": "gpu", "steps": [], "output": {"acceleration": {"mode": "gpu"}}}
    )
    j = store.create(
        JobSpec(source_url="https://youtu.be/L6imSiJLd9Q", title="gpu", preset_version_id=p.id)
    )
    old = store.claim_next("gpu-test")
    store.transition(old.id, old.revision, "PAUSED", "user_canceled")
    claimed = store.claim_next("gpu-test")
    service.hardware = SimpleNamespace(
        check=lambda *a: HardwareReport(
            available=False,
            reason="driver_incompatible",
            fingerprint="a" * 64,
            checked_at="2026-11-07T00:00:00+00:00",
        )
    )

    def forbidden(*a, **kw):
        raise AssertionError("strict GPU cannot fall back to a CPU render")

    monkeypatch.setattr("fabu.media.service.render", forbidden)
    with pytest.raises(ProcessingPaused, match="gpu_driver_incompatible"):
        service.process_job(claimed)
    assert store.get(j.id).pause_reason == "gpu_driver_incompatible"
    assert store.get(j.id).source_manifest and not store.get(j.id).output_manifest


def test_legacy_preset_semantics_and_parameter_separation(tmp_path):
    from types import SimpleNamespace

    from pydantic import ValidationError

    from fabu.media.compiler import semantic_fingerprint
    from fabu.media.models import ENGINE_VERSION, ExecutionProfile

    old = {
        "codec": "libx265",
        "preset": "fast",
        "crf": 20,
        "fps": 30,
        "pixel_format": "yuv420p",
        "size_policy": "auto",
    }
    payload = {"steps": [], "output": old, "assets": {}, "engine": ENGINE_VERSION}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert (
        semantic_fingerprint(SimpleNamespace(output={}, steps=[]), {}, ENGINE_VERSION) == expected
    )
    with pytest.raises(ValidationError):
        OutputProfile(acceleration={"mode": "gpu", "crf": 20})
    with pytest.raises(ValidationError):
        ExecutionProfile(path="cpu", encoder="libx265", reason="cpu_requested", cq=20)


@pytest.mark.media
def test_execution_approval_matches_current_tools_without_cross_backend_reuse(context, tmp_path):
    from fabu.media.compiler import bind_tool_identity, compile_plan
    from fabu.media.encoders import bind_execution
    from fabu.media.hardware import HardwareReport
    from fabu.media.models import InputBundle, compatibility_version
    from fabu.media.preview import build_preview
    from fabu.media.probe import probe_media

    service, store, presets, approvals, _, job, _ = context
    source = service.source_loader(job)
    preset = presets.save_draft(
        None, {"name": "auto-approval", "steps": [], "output": {"acceleration": {"mode": "auto"}}}
    )
    info = probe_media(source.source.local_path, service.tools.probe)
    base = bind_tool_identity(
        compile_plan(InputBundle(source=info), preset), preset, service.tools.identity()
    )
    no = HardwareReport(
        available=False,
        reason="no_device",
        fingerprint="a" * 64,
        checked_at="2026-11-07T00:00:00+00:00",
    )
    plan = bind_execution(base, no, preset=preset)
    approvals.current_tools = service.tools.identity
    preview = build_preview(
        plan, windows=[(0, 400)], workspace=tmp_path / "gpu-preview", tools=service.tools
    )
    approvals.record_preview(preview, preset.id)
    approvals.approve_preview(preview.id)
    assert approvals.approval_matches(preset, {}, compatibility_version(plan))
    yes = HardwareReport(
        available=True,
        reason="nvenc_verified",
        fingerprint="b" * 64,
        checked_at="2026-11-07T00:00:00+00:00",
    )
    gpu = bind_execution(base, yes, preset=preset)
    assert not approvals.approval_matches(preset, {}, compatibility_version(gpu))
    approvals.current_tools = lambda: {"ffmpeg": "f" * 64, "ffprobe": "e" * 64}
    assert not approvals.approval_matches(preset, {}, compatibility_version(plan))
