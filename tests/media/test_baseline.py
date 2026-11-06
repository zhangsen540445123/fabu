from fractions import Fraction
from threading import Event

import pytest

from fabu.media.probe import AudioInfo, ColorInfo, MediaInfo


def info_fixture(tmp_path):
    p = tmp_path / "source.mp4"
    p.write_bytes(b"original bytes")
    from fabu.assets import sha256_file

    return MediaInfo(
        path=p,
        sha256=sha256_file(p),
        duration=Fraction(10),
        video_stream_index=0,
        codec="h264",
        coded_width=1280,
        coded_height=720,
        sar=Fraction(1),
        rotation_deg=0,
        fps=Fraction(30),
        pixel_format="yuv420p",
        color=ColorInfo(space="bt709", transfer="bt709", primaries="bt709", range="tv"),
        start_time=Fraction(0),
        audio=AudioInfo(
            stream_index=1,
            codec="aac",
            sample_rate=44100,
            channels=2,
            channel_layout="stereo",
            duration=Fraction(10),
            start_time=Fraction(0),
        ),
    )


def test_baseline_preserves_approved_profile(tmp_path):
    from fabu.media.baseline import build_baseline_plan, build_render_command

    plan = build_baseline_plan(info_fixture(tmp_path))
    assert plan.output_profile.crf == 20 and plan.output_profile.preset == "fast"
    assert plan.output_profile.fps == 30 and plan.output_profile.pixel_format == "yuv420p"
    assert plan.output_size.width == 2560 and plan.output_size.height == 1440
    assert plan.expected_duration_ms == 10000 and plan.audio_profile.bitrate_kbps == 192
    assert plan.stages[0].kind == "baseline"
    assert not any("hflip" in f or "crop" in f for f in plan.stages[0].video_filters)
    command = build_render_command(plan, tmp_path / "candidate.mp4", tmp_path / "progress.txt")
    assert "-noautorotate" in command.args and "-n" in command.args
    assert command.args[command.args.index("-c:v") + 1] == "libx265"
    assert command.args[command.args.index("-crf") + 1] == "20"
    assert "-shortest" not in command.args
    assert command.args[-1] == str(tmp_path / "candidate.mp4")


def test_baseline_mono_and_rotation(tmp_path):
    from fabu.media.baseline import build_baseline_plan

    info = info_fixture(tmp_path)
    info = info.model_copy(
        update={
            "rotation_deg": 90,
            "audio": info.audio.model_copy(
                update={"channels": 1, "channel_layout": "mono", "sample_rate": 48000}
            ),
        }
    )
    plan = build_baseline_plan(info)
    assert plan.audio_profile.sample_rate == 48000 and plan.audio_profile.bitrate_kbps == 128
    assert "transpose=cclock" in plan.stages[0].video_filters
    assert plan.output_size.width == 1440 and plan.output_size.height == 2560


def test_plan_digest_detects_mutation(tmp_path):
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.models import PlanInvalid, verify_plan_digest

    plan = build_baseline_plan(info_fixture(tmp_path))
    verify_plan_digest(plan)
    plan.stages[0].video_filters.append("hflip")
    with pytest.raises(PlanInvalid):
        verify_plan_digest(plan)


def test_silent_main_not_auto_accepted(tmp_path):
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.probe import UnsupportedMedia

    with pytest.raises(UnsupportedMedia, match="audio"):
        build_baseline_plan(info_fixture(tmp_path).model_copy(update={"audio": None}))


def test_render_cancellation_and_bad_space_do_not_produce_formal_file(tmp_path, monkeypatch):
    from collections import namedtuple

    import fabu.media.renderer as module
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.renderer import RenderFailed, render

    plan = build_baseline_plan(info_fixture(tmp_path))
    cancel = Event()
    cancel.set()
    with pytest.raises(RenderFailed, match="cancel"):
        render(plan, tmp_path / "job", cancel, tools=None)
    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: usage(1000, 999, 1))
    with pytest.raises(RenderFailed, match="space"):
        render(plan, tmp_path / "job", Event(), tools=None)
    assert not list(tmp_path.rglob("candidate-*.mp4"))


def test_render_modified_source_does_not_start(tmp_path):
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.renderer import RenderFailed, render

    plan = build_baseline_plan(info_fixture(tmp_path))
    plan.source.path.write_bytes(b"changed bytes")
    with pytest.raises(RenderFailed, match="source"):
        render(plan, tmp_path / "job", Event(), tools=None)


def test_parse_progress_clamps_to_nonfinal(tmp_path):
    from fabu.media.renderer import read_progress

    p = tmp_path / "progress.txt"
    p.write_text("out_time_us=2500000\nprogress=continue\n")
    assert read_progress(p, 10000) == 25.0
    p.write_text("out_time_us=12000000\nprogress=end\n")
    assert read_progress(p, 10000) == 99.0  # 100 only after independent validation


def test_validate_mismatched_output_profile_fails(tmp_path):
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.validator import OutputInvalid, validate_metadata

    info = info_fixture(tmp_path)
    plan = build_baseline_plan(info)
    with pytest.raises(OutputInvalid):
        validate_metadata(info, plan)
